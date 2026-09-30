#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: (ALE-1.1 AND GPL-3.0-only)
# Copyright (c) 2022-2025 wuyilingwei
#
# This file is licensed under the ANTI-LABOR EXPLOITATION LICENSE 1.1
# in combination with GNU General Public License v3.0.
# See .github/LICENSE for full license text.
"""
Automated translation script for Timberborn mods
This script processes TOML files and translates missing language entries using LLM
"""

import os
import sys
import toml
import time
import logging
import argparse
import json
import re
import threading
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait as fut_wait
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

# Add util to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'util'))
from translator import TranslatorLLM
from translation_prompt import system_prompt_parts


# Global variable to store language names loaded from config
LANGUAGE_NAMES = {}

# Global variable to store glossary
GLOSSARY = {}


class ThreadWorkTracker:
    """Tracks per-thread work assignments and timing for diagnostic reporting.

    Every file-level and entry-level work assignment is recorded so that the
    final summary shows exactly which thread handled which work and how long
    each unit took.  All public methods are thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # {thread_name: [{"type": "file"|"entry"|"language", "name": ...,
        #                  "start": float, "end": float|None, "detail": ...}]}
        self._records: Dict[str, List[Dict]] = {}
        self._start_time = time.time()
        self._logger = logging.getLogger("ThreadWorkTracker")

    # ------------------------------------------------------------------ #
    # Recording helpers                                                    #
    # ------------------------------------------------------------------ #
    def _thread_name(self) -> str:
        return threading.current_thread().name

    def record_start(self, work_type: str, name: str, detail: str = "") -> None:
        """Record the start of a work unit on the current thread."""
        thread = self._thread_name()
        record = {
            "type": work_type,
            "name": name,
            "start": time.time(),
            "end": None,
            "detail": detail,
        }
        with self._lock:
            self._records.setdefault(thread, []).append(record)
        self._logger.debug(
            f"[work-start] type={work_type} name={name} thread={thread}"
            + (f" detail={detail}" if detail else "")
        )

    def record_end(self, work_type: str, name: str) -> None:
        """Record the end of the most recent matching work unit."""
        thread = self._thread_name()
        now = time.time()
        with self._lock:
            for rec in reversed(self._records.get(thread, [])):
                if rec["type"] == work_type and rec["name"] == name and rec["end"] is None:
                    rec["end"] = now
                    break
        self._logger.debug(
            f"[work-end] type={work_type} name={name} thread={thread}"
        )

    # ------------------------------------------------------------------ #
    # Summary                                                              #
    # ------------------------------------------------------------------ #
    def summary(self) -> str:
        """Return a human-readable summary of all thread work assignments."""
        wall_time = time.time() - self._start_time
        lines: List[str] = []
        lines.append("=" * 72)
        lines.append("THREAD WORK ALLOCATION SUMMARY")
        lines.append(f"Total wall time: {wall_time:.1f}s")
        lines.append("=" * 72)

        with self._lock:
            snapshot = {t: list(recs) for t, recs in self._records.items()}

        for thread_name in sorted(snapshot):
            records = snapshot[thread_name]
            lines.append(f"\n--- Thread: {thread_name} ---")
            lines.append(f"  Total work units: {len(records)}")

            # Group by type
            by_type: Dict[str, List[Dict]] = {}
            for rec in records:
                by_type.setdefault(rec["type"], []).append(rec)

            for wtype in ("file", "entry", "language"):
                recs = by_type.get(wtype, [])
                if not recs:
                    continue
                completed = [r for r in recs if r["end"] is not None]
                total_time = sum((r["end"] - r["start"]) for r in completed)
                lines.append(f"  [{wtype}] count={len(recs)}, completed={len(completed)}, total_time={total_time:.1f}s")
                for r in recs:
                    duration = f"{r['end'] - r['start']:.2f}s" if r["end"] else "IN PROGRESS"
                    detail_str = f" ({r['detail']})" if r.get("detail") else ""
                    lines.append(f"    - {r['name']}{detail_str}: {duration}")

        lines.append("\n" + "=" * 72)
        return "\n".join(lines)


# Global tracker instance — initialised lazily by process_all_files()
_work_tracker: Optional[ThreadWorkTracker] = None


def load_language_names_from_config(config: Dict) -> Dict[str, str]:
    """Load language code to full name mapping from configuration"""
    return config.get("languages", {}).get("locale_names", {})


def setup_logging(log_level: str = "INFO", thread_log_file: Optional[str] = None) -> None:
    """Setup logging configuration

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        thread_log_file: Optional path for a dedicated thread-allocation /
            work-info log file.  This file always captures at DEBUG level so
            that every thread lifecycle event and work assignment is recorded
            regardless of the console log level.
    """
    # Convert string to logging level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Format that includes the thread name for attributable multi-threaded output
    log_format = '%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    logging.basicConfig(
        level=numeric_level,
        format=log_format,
        datefmt=date_format,
    )

    # Dedicated thread/work log file — captures everything at DEBUG level
    if thread_log_file:
        os.makedirs(os.path.dirname(thread_log_file) or '.', exist_ok=True)
        thread_handler = logging.FileHandler(thread_log_file, mode='w', encoding='utf-8')
        thread_handler.setLevel(logging.DEBUG)
        thread_handler.setFormatter(
            logging.Formatter(log_format, datefmt=date_format)
        )
        logging.getLogger().addHandler(thread_handler)
        logging.getLogger().info(f"Thread/work log file: {thread_log_file}")


def load_config(config_path: str) -> Dict:
    """Load configuration from TOML file"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = toml.load(f)
    
    return config


def load_target_languages(config: Dict) -> List[str]:
    """Load target languages from configuration"""
    languages = config.get("languages", {}).get("supported", [])
    
    if not languages:
        raise ValueError("No languages defined in configuration")
    
    logging.info(f"Target languages: {', '.join(languages)}")
    return languages


def load_ingame_languages(config: Dict) -> List[str]:
    """
    Return languages that are in supported but NOT in game_supported.
    These are the only languages translated in _ingame*.toml files.
    """
    supported = set(config.get("languages", {}).get("supported", []))
    game_supported = set(config.get("languages", {}).get("game_supported", []))
    ingame_only = [lang for lang in config.get("languages", {}).get("supported", [])
                   if lang not in game_supported]
    logging.info(f"Ingame-only languages (supported ∖ game_supported): {', '.join(ingame_only)}")
    return ingame_only


def load_glossary(glossary_path: str) -> Dict[str, Dict[str, str]]:
    """
    Load glossary from TOML file
    
    Args:
        glossary_path: Path to _glossary.toml file
        
    Returns:
        Dictionary mapping terms to their translations in different languages
        Format: {"Term": {"zhCN": "翻译", "zhTW": "翻譯", ...}}
    """
    logger = logging.getLogger("load_glossary")
    
    if not os.path.exists(glossary_path):
        logger.warning(f"Glossary file not found: {glossary_path}")
        return {}
    
    try:
        with open(glossary_path, 'r', encoding='utf-8') as f:
            glossary = toml.load(f)
        logger.info(f"Loaded glossary with {len(glossary)} terms")
        return glossary
    except Exception as e:
        logger.error(f"Failed to load glossary: {e}")
        return {}


def merge_glossaries(global_glossary: Dict[str, Dict[str, str]], 
                     local_glossary: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """
    Merge global and local glossaries, with local taking priority
    
    Args:
        global_glossary: Global glossary dictionary
        local_glossary: Local (mod-specific) glossary dictionary
        
    Returns:
        Merged glossary with local terms overriding global terms
        
    Note:
        This function merges glossaries at the term level, but translation lookup
        respects the 'all' keyword priority: local_specific > local_all > global_specific > global_all
        The merged dictionary stores metadata to distinguish local vs global sources.
    """
    if not global_glossary:
        return local_glossary or {}
    if not local_glossary:
        return global_glossary
    
    # Start with a copy of global glossary
    merged = {}
    
    # Add all global terms first
    for term, translations in global_glossary.items():
        merged[term] = {'_global': translations.copy()}
    
    # Add or merge local glossary terms
    for term, translations in local_glossary.items():
        if term in merged:
            # Term exists in both - store local separately
            merged[term]['_local'] = translations.copy()
        else:
            # New term from local glossary
            merged[term] = {'_local': translations.copy()}
    
    return merged


def get_glossary_translation(
    term_data: Dict,
    target_language: str
) -> Optional[str]:
    """
    Get translation for a term with proper priority handling
    
    Priority order:
    1. Local glossary with specific language
    2. Local glossary with 'all' keyword
    3. Global glossary with specific language
    4. Global glossary with 'all' keyword
    
    Args:
        term_data: Dictionary containing '_local' and/or '_global' glossary data
        target_language: Target language code
        
    Returns:
        Translation string or None if not found
    """
    # Extract special keys
    local_trans = term_data.get('_local', {})
    global_trans = term_data.get('_global', {})
    
    # Remove special keys from translations (skip_hints, fuzzy_tolerance, translations)
    def clean_translations(trans_dict):
        if not trans_dict:
            return {}
        if 'translations' in trans_dict:
            return trans_dict.get('translations', {})
        return {k: v for k, v in trans_dict.items() if k not in ['skip_hints', 'fuzzy_tolerance', '_local', '_global']}
    
    local_clean = clean_translations(local_trans)
    global_clean = clean_translations(global_trans)
    
    # Priority 1: Local specific language
    if target_language in local_clean:
        return local_clean[target_language]
    
    # Priority 2: Local 'all'
    if 'all' in local_clean:
        return local_clean['all']
    
    # Priority 3: Global specific language
    if target_language in global_clean:
        return global_clean[target_language]
    
    # Priority 4: Global 'all'
    if 'all' in global_clean:
        return global_clean['all']
    
    return None


def fuzzy_match_term(text: str, term: str, tolerance: int = 2) -> bool:
    """
    Check if a term fuzzy matches text with a given tolerance (case-insensitive)
    For terms with 10+ characters, allows up to 'tolerance' character differences
    
    Args:
        text: Text to search in
        term: Term to match
        tolerance: Maximum character differences allowed (default: 2)
        
    Returns:
        True if fuzzy match found, False otherwise
    """
    if len(term) < 10:
        # Only exact match for short terms (case-insensitive)
        import re
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        return pattern.search(text) is not None
    
    # For longer terms, check fuzzy match (case-insensitive)
    from difflib import SequenceMatcher
    
    # Check if term appears with minor variations
    text_lower = text.lower()
    term_lower = term.lower()
    
    # Try to find the term or similar substring
    for i in range(len(text) - len(term) + tolerance + 1):
        for length in range(max(len(term) - tolerance, 1), len(term) + tolerance + 1):
            if i + length > len(text):
                continue
            substring = text[i:i+length]
            
            # Calculate similarity
            matcher = SequenceMatcher(None, term_lower, substring.lower())
            ratio = matcher.ratio()
            
            # If very similar (allowing for tolerance characters difference)
            max_diff = tolerance / len(term)
            if ratio >= (1 - max_diff):
                return True
    
    return False


def generate_glossary_hints(
    text: str,
    target_language: str,
    glossary: Dict[str, Dict[str, str]],
    language_priority: List[str],
    default_fuzzy_tolerance: int = 2
) -> Tuple[str, List[str]]:
    """
    Apply glossary replacements and generate hints for missing translations
    
    Args:
        text: Source text to process
        target_language: Target language code
        glossary: Glossary dictionary with English keys and translations
        language_priority: Ordered list of languages (only show first available)
        default_fuzzy_tolerance: Default tolerance for fuzzy matching (default: 2 characters)
        
    Returns:
        Tuple of (preprocessed_text, list of glossary hints)
        
    Note:
        Supports 'all' keyword as fallback when specific language is not defined.
        Priority: specific_language > 'all' keyword
    """
    if not glossary or not text:
        return text, []
    
    # Sort terms by length (longest first) to avoid partial replacements
    sorted_terms = sorted(glossary.keys(), key=len, reverse=True)
    
    result = text
    hints = []
    processed_terms = set()
    
    for term in sorted_terms:
        term_data = glossary.get(term, {})
        
        # Extract metadata from term_data  
        skip_hints = False
        fuzzy_tolerance = default_fuzzy_tolerance
        
        # Check for metadata in either _local or _global
        for source_key in ['_local', '_global']:
            source_data = term_data.get(source_key, {})
            if 'skip_hints' in source_data:
                skip_hints = source_data.get('skip_hints', False)
            if 'fuzzy_tolerance' in source_data:
                fuzzy_tolerance = source_data.get('fuzzy_tolerance', default_fuzzy_tolerance)
        
        # Check for exact match (case-insensitive)
        import re
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        match = pattern.search(text)
        
        if match:
            # Use new priority-based lookup
            translation_found = get_glossary_translation(term_data, target_language)
            
            if translation_found:
                # Exact match with translation available - replace (case-insensitive)
                result = pattern.sub(translation_found, result)
                processed_terms.add(term)
            elif not skip_hints:
                # Exact match but no translation for target language - add hint
                # Get all available translations for hint
                local_trans = term_data.get('_local', {})
                global_trans = term_data.get('_global', {})
                
                # Collect all translations
                all_translations = {}
                for trans_dict in [global_trans, local_trans]:
                    if 'translations' in trans_dict:
                        all_translations.update(trans_dict['translations'])
                    else:
                        all_translations.update({k: v for k, v in trans_dict.items() 
                                                if k not in ['skip_hints', 'fuzzy_tolerance', '_local', '_global']})
                
                if all_translations:
                    # Only show the first available language from priority list
                    first_available = None
                    for lang in language_priority:
                        if lang in all_translations:
                            first_available = lang
                            break
                    
                    if first_available:
                        hint = f'Term "{term}" in {first_available}: {all_translations[first_available]}'
                        hints.append(hint)
                        processed_terms.add(term)
        
        # Check for fuzzy match (only for longer terms not already processed)
        elif len(term) >= 10 and term not in processed_terms:
            if fuzzy_match_term(text, term, fuzzy_tolerance):
                # Fuzzy match found - add as hint (don't replace)
                # Use new priority-based lookup
                translation_found = get_glossary_translation(term_data, target_language)
                
                if translation_found:
                    hint = f'Term "{term}" (fuzzy match) translates to "{translation_found}" in {target_language}'
                    hints.append(hint)
                elif not skip_hints:
                    # Get all available translations for hint
                    local_trans = term_data.get('_local', {})
                    global_trans = term_data.get('_global', {})
                    
                    # Collect all translations
                    all_translations = {}
                    for trans_dict in [global_trans, local_trans]:
                        if 'translations' in trans_dict:
                            all_translations.update(trans_dict['translations'])
                        else:
                            all_translations.update({k: v for k, v in trans_dict.items() 
                                                    if k not in ['skip_hints', 'fuzzy_tolerance', '_local', '_global']})
                    
                    if all_translations:
                        # Only show the first available language from priority list
                        first_available = None
                        for lang in language_priority:
                            if lang in all_translations:
                                first_available = lang
                                break
                        
                        if first_available:
                            hint = f'Term "{term}" (fuzzy match) in {first_available}: {all_translations[first_available]}'
                            hints.append(hint)
                processed_terms.add(term)
    
    return result, hints


def apply_glossary_to_source(
    text: str, 
    target_language: str, 
    glossary: Dict[str, Dict[str, str]],
    language_priority: Optional[List[str]] = None
) -> str:
    """
    Apply glossary replacements to source text before translation
    Replaces English glossary terms with their target language translations
    Terms are replaced from longest to shortest to avoid partial replacements
    
    Args:
        text: Source text to process (in English)
        target_language: Target language code for glossary lookup
        glossary: Glossary dictionary with English keys and language translations
        language_priority: Ordered list of languages for hint priority
        
    Returns:
        Text with English glossary terms replaced with target language translations
    """
    if not glossary or not text:
        return text
    
    # Use the new generate_glossary_hints function but only return preprocessed text
    if language_priority is None:
        language_priority = [target_language]
    
    preprocessed_text, _ = generate_glossary_hints(text, target_language, glossary, language_priority)
    return preprocessed_text


def apply_glossary(text: str, target_language: str, glossary: Dict[str, Dict[str, str]]) -> str:
    """
    Apply glossary replacements to translated text for a specific language
    
    Args:
        text: Text to process (translated text)
        target_language: Target language code
        glossary: Glossary dictionary (merged global+local with _local/_global structure)
        
    Returns:
        Text with glossary terms replaced
        
    Note:
        Supports 'all' keyword as fallback when specific language is not defined.
        Priority: local_specific > local_all > global_specific > global_all
    """
    if not glossary or not text:
        return text
    
    # Sort terms by length (longest first) to avoid partial replacements
    sorted_terms = sorted(glossary.keys(), key=len, reverse=True)
    
    result = text
    for term in sorted_terms:
        term_data = glossary.get(term, {})
        
        # Use new priority-based lookup
        translation_to_use = get_glossary_translation(term_data, target_language)
        
        if translation_to_use:
            # Case-insensitive replacement while preserving original case
            import re
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            result = pattern.sub(translation_to_use, result)
    
    return result


def _is_table_header(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("[") and stripped.rstrip().endswith("]")


def _normalize_glossary_header_line(line: str) -> str:
    stripped = line.strip()
    match = re.match(r"^\[_meta\.glossary\.(.+)\]$", stripped)
    if not match:
        return line

    key_part = match.group(1)
    if key_part.startswith('"') and key_part.endswith('"'):
        return line

    # Preserve original line ending and leading whitespace
    line_ending = ""
    if line.endswith("\r\n"):
        line_ending = "\r\n"
    elif line.endswith("\n"):
        line_ending = "\n"

    leading_ws = line[:len(line) - len(line.lstrip())]
    escaped_key = key_part.replace("\\", "\\\\").replace('"', "\\\"")
    return f'{leading_ws}[_meta.glossary."{escaped_key}"]{line_ending}'


def reorder_glossary_blocks(toml_text: str) -> str:
    """
    Ensure _meta.glossary blocks are placed immediately after _meta and
    normalize glossary keys to quoted form for consistent style.
    """
    lines = toml_text.splitlines(keepends=True)
    if not lines:
        return toml_text

    blocks = []
    current_header = None
    current_lines: List[str] = []

    for line in lines:
        if _is_table_header(line):
            if current_lines:
                blocks.append((current_header, current_lines))
            current_header = line
            current_lines = [line]
        else:
            if not current_lines:
                current_header = None
                current_lines = [line]
            else:
                current_lines.append(line)

    if current_lines:
        blocks.append((current_header, current_lines))

    # Normalize glossary header lines
    normalized_blocks = []
    for header, block_lines in blocks:
        if header is not None:
            normalized_header = _normalize_glossary_header_line(header)
            if normalized_header != header:
                block_lines = [normalized_header] + block_lines[1:]
                header = normalized_header
        normalized_blocks.append((header, block_lines))

    # If there is no _meta section, keep original order
    has_meta = any((h and h.strip() == "[_meta]") for h, _ in normalized_blocks)
    if not has_meta:
        return "".join("".join(lines) for _, lines in normalized_blocks)

    preamble = []
    meta_block = None
    glossary_blocks = []
    other_blocks = []

    for header, block_lines in normalized_blocks:
        if header is None:
            preamble.append(block_lines)
            continue
        stripped = header.strip()
        if stripped == "[_meta]":
            meta_block = block_lines
        elif stripped.startswith("[_meta.glossary."):
            glossary_blocks.append(block_lines)
        else:
            other_blocks.append(block_lines)

    if meta_block is None or not glossary_blocks:
        return "".join("".join(lines) for _, lines in normalized_blocks)

    # Add blank line between glossary and other blocks if needed
    if other_blocks:
        first_other_block = other_blocks[0]
        if first_other_block and first_other_block[0] and not first_other_block[0].strip().startswith("\n"):
            # Add blank line if the first line of other blocks is not empty
            first_other_block = ["\n"] + first_other_block
            other_blocks = [first_other_block] + other_blocks[1:]

    ordered_blocks = preamble + [meta_block] + glossary_blocks + other_blocks
    return "".join("".join(block) for block in ordered_blocks)


def reorder_language_fields(toml_text: str, language_order: List[str]) -> str:
    """
    Reorder language code fields in TOML text according to the specified order
    
    Args:
        toml_text: Original TOML text
        language_order: List of language codes in desired order
        
    Returns:
        TOML text with language fields reordered
    """
    if not toml_text or not language_order:
        return toml_text
    
    lines = toml_text.splitlines(keepends=True)
    if not lines:
        return toml_text
    
    blocks = []
    current_header = None
    current_lines: List[str] = []
    
    # Parse TOML into blocks
    for line in lines:
        if _is_table_header(line):
            if current_lines:
                blocks.append((current_header, current_lines))
            current_header = line
            current_lines = [line]
        else:
            if not current_lines:
                current_header = None
                current_lines = [line]
            else:
                current_lines.append(line)
    
    if current_lines:
        blocks.append((current_header, current_lines))
    
    # Process each block to reorder language fields
    processed_blocks = []
    for header, block_lines in blocks:
        if header is None:
            # Preamble block, keep as is
            processed_blocks.append(block_lines)
            continue
            
        # Check if this is a translation entry block (not _meta)
        header_stripped = header.strip()
        if header_stripped.startswith('[_meta'):
            # Meta block, keep as is
            processed_blocks.append(block_lines)
            continue
        
        # This is a translation entry, reorder language fields
        reordered_lines = _reorder_fields_in_block(block_lines, language_order)
        processed_blocks.append(reordered_lines)
    
    return "".join("".join(block) for block in processed_blocks)


def _reorder_fields_in_block(block_lines: List[str], language_order: List[str]) -> List[str]:
    """
    Reorder language fields within a single TOML block
    
    Args:
        block_lines: Lines of the TOML block
        language_order: Desired order of language codes
        
    Returns:
        Block lines with language fields reordered
    """
    if not block_lines or not language_order:
        return block_lines
    
    # Separate header, non-language fields, language fields, and comments
    header_line = block_lines[0] if block_lines and _is_table_header(block_lines[0]) else None
    result_lines = []
    
    if header_line:
        result_lines.append(header_line)
        content_lines = block_lines[1:]
    else:
        content_lines = block_lines
    
    # Parse field lines and group them
    non_lang_fields = []  # Non-language fields (raw, status, etc.)
    lang_fields = {}      # Language fields by language code
    comment_lines = []    # Comments and empty lines
    
    for line in content_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            comment_lines.append(line)
            continue
        
        # Check if this is a field assignment
        if '=' in line:
            field_name = line.split('=')[0].strip()
            if field_name in language_order:
                lang_fields[field_name] = line
            else:
                non_lang_fields.append(line)
        else:
            comment_lines.append(line)
    
    # Add non-language fields first
    result_lines.extend(non_lang_fields)
    
    # Add language fields in the specified order
    for lang_code in language_order:
        if lang_code in lang_fields:
            result_lines.append(lang_fields[lang_code])
    
    # Add comment lines at the end
    result_lines.extend(comment_lines)
    
    return result_lines


def build_translation_prompt(
    key: str,
    new_text: str,
    mod_name: str,
    target_language: str,
    raw: Optional[str] = None,
    current_translation: Optional[str] = None,
    prompt: Optional[str] = None,
    specific_prompt: Optional[str] = None,
    glossary_hints: Optional[List[str]] = None
) -> Tuple[List[Dict], str]:
    """Keep reusable references separate from entry-specific source and context."""
    parts = system_prompt_parts(target_language, LANGUAGE_NAMES.get(target_language, target_language))
    system = [{'type': 'text', 'text': part, 'prompt_cache_breakpoint': {'mode': 'explicit'}}
              for part in parts]
    context = {'mod': mod_name, 'key': key, 'field_hint': prompt,
               'specific_note': specific_prompt, 'glossary_references': glossary_hints or []}
    if raw is not None:
        context.update(old_source_text=raw, current_translation=current_translation)
    return system, json.dumps({'source_text': new_text, 'context': context}, ensure_ascii=False)


def strip_extra_quotes(text: str, reference_text: str) -> str:
    """
    Strip extra quotes from translated text if the reference doesn't have them.
    
    This handles cases where LLM adds extra quotes like:
    - raw: "Mostly helpful billboards" -> zhTW: '"大多數有幫助的廣告牌"'
    
    Args:
        text: Translated text that might have extra quotes
        reference_text: Reference text (usually raw field) to check quote pattern
        
    Returns:
        Text with extra quotes removed if appropriate
    """
    if not text or not reference_text:
        return text
    
    # Check if reference text starts and ends with quotes
    ref_has_quotes = reference_text.startswith('"') and reference_text.endswith('"')
    
    # If translation has quotes but reference doesn't, remove them
    if not ref_has_quotes and text.startswith('"') and text.endswith('"') and len(text) > 2:
        # Remove leading and trailing quotes
        text = text[1:-1]
    
    return text


def translate_entry(
    translator: TranslatorLLM,
    key: str,
    entry: Dict,
    target_lang: str,
    mod_name: str,
    prompt: Optional[str] = None,
    glossary: Optional[Dict[str, Dict[str, str]]] = None,
    language_priority: Optional[List[str]] = None
) -> Optional[str]:
    """
    Translate a single entry for a specific language
    
    Args:
        prompt: Optional prompt hint for translation context
        glossary: Optional merged glossary (global + local) to apply before translation
        language_priority: Ordered list of languages for glossary hint priority
    
    Returns:
        Translated text or None if translation not needed/failed
    """
    logger = logging.getLogger("translate_entry")
    
    # Check if this entry should use copy mode (for symbolic fields)
    copy_mode = entry.get("copy", False)
    
    # Determine the text to translate
    has_new_field = "new" in entry
    raw_text = entry.get("raw", "")
    
    # Check if translation exists for this language
    # Empty string is valid translation if raw text is also empty
    has_translation = target_lang in entry and (
        entry.get(target_lang) or 
        (entry.get(target_lang) == "" and raw_text.strip() == "")
    )
    
    # Skip if translation exists and no update needed
    if not has_new_field and has_translation:
        return None
    
    # Handle copy mode: directly copy source text without translation
    if copy_mode:
        source_text = entry.get("new") if has_new_field else entry.get("raw", "")
        logger.debug(f"Copy mode for {key}: copying '{source_text}' to {target_lang}")
        return source_text
    
    # Determine source text: use "new" if available, otherwise use "raw"
    if has_new_field:
        text_to_translate = entry.get("new")
        if text_to_translate is None:
            logger.warning(f"Entry {key} has 'new' field but value is None")
            return None
    else:
        # Missing translation case - use raw text
        text_to_translate = entry.get("raw")
        if text_to_translate is None:
            logger.warning(f"Entry {key} has no 'raw' or 'new' field to translate from")
            return None
        # If raw text is empty, return empty string (valid empty translation)
        if not text_to_translate.strip():
            return ""
    
    # Apply glossary to source text BEFORE translation with hints (if translating a "new" field)
    preprocessed_text = text_to_translate
    glossary_hints = []
    
    if has_new_field and glossary:
        if language_priority is None:
            language_priority = [target_lang]
        
        preprocessed_text, glossary_hints = generate_glossary_hints(
            text_to_translate, 
            target_lang, 
            glossary,
            language_priority
        )
        
        if preprocessed_text != text_to_translate:
            logger.debug(f"Glossary preprocessing for {target_lang}: '{text_to_translate}' -> '{preprocessed_text}'")
        
        if glossary_hints:
            logger.debug(f"Generated {len(glossary_hints)} glossary hints for {key}")
    
    # Get context information
    raw = entry.get("raw")
    current_translation = entry.get(target_lang)
    specific_prompt = entry.get("prompt")
    
    # Build prompts with glossary hints
    system_prompt, user_prompt = build_translation_prompt(
        key=key,
        new_text=preprocessed_text,  # Use preprocessed text with glossary applied
        mod_name=mod_name,
        target_language=target_lang,
        raw=raw if has_new_field and raw != text_to_translate else None,  # Only show old text if different from new
        current_translation=current_translation,
        prompt=prompt,
        specific_prompt=specific_prompt,
        glossary_hints=glossary_hints if glossary_hints else None
    )
    
    # Translate with preprocessed text
    if _work_tracker:
        _work_tracker.record_start("language", f"{key}/{target_lang}", detail=f"text_len={len(preprocessed_text)}")
    translation = translator.translate(
        text=preprocessed_text,  # Send preprocessed text to LLM
        target_language=target_lang,
        system_prompt=system_prompt,
        user_prompt=user_prompt
    )
    if _work_tracker:
        _work_tracker.record_end("language", f"{key}/{target_lang}")
    
    # Validate translation result
    # Empty translation is only valid if the original raw text is also empty
    if translation is not None:
        if not translation and text_to_translate.strip():
            # Translation is empty but original text is not - invalid result
            logger.warning(f"Translation returned empty for non-empty original text in {key}")
            return None
        
        # Strip extra quotes if present and not in original
        translation = strip_extra_quotes(translation, text_to_translate)
        if not translation.strip() and text_to_translate.strip():
            logger.warning(f"Translation became empty after removing wrappers in {key}")
            return None
        
        logger.info(f"[{key}] [{target_lang}] {text_to_translate!r} -> {translation!r}")
    
    return translation


def process_toml_file(
    toml_path: str,
    translator: TranslatorLLM,
    target_languages: List[str],
    dry_run: bool = False,
    max_entry_threads: Union[int, Callable[[], int]] = 1,
    max_lang_threads: int = 0
) -> Tuple[int, int]:
    """
    Process a single TOML file and translate missing entries.

    Args:
        toml_path: Path to the TOML file
        translator: Translator instance
        target_languages: List of target language codes
        dry_run: If True, don't write changes
        max_entry_threads: Maximum number of entries to process concurrently
            within this file.  May be a plain ``int`` or a zero-argument
            callable that returns an ``int``.  The callable form is evaluated
            **before each batch of entries** so that thread allocation adjusts
            dynamically as sibling files finish (e.g. the last 3 files scale
            up from 1 to 10 entry-threads automatically).  When > 1, multiple
            entries are translated in parallel, with each entry still
            parallelising its own languages internally.  A threading lock
            serialises all writes so checkpoint saves remain atomic.

    Returns:
        Tuple of (translations_made, entries_processed)
    """
    logger = logging.getLogger("process_toml_file")
    filename = os.path.basename(toml_path)

    # Normalise to a callable so the rest of the function always calls it.
    if callable(max_entry_threads):
        _get_threads = max_entry_threads
    else:
        _n = max_entry_threads
        _get_threads = lambda: _n  # noqa: E731

    logger.info(f"Processing {filename}")
    
    # Load TOML file
    try:
        with open(toml_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)
    except Exception as e:
        logger.error(f"Failed to load {filename}: {e}")
        return 0, 0
    
    # Extract mod metadata from _meta section (always required now)
    meta_section = data.get("_meta", {})
    mod_name = meta_section.get("name", filename.replace('.toml', ''))
    prompt = meta_section.get("prompt")
    
    # Extract mod-local glossary from _meta section
    local_glossary = meta_section.get("glossary", {})
    
    # Merge global and local glossaries (local takes priority)
    merged_glossary = merge_glossaries(GLOSSARY, local_glossary)
    if local_glossary:
        logger.info(f"Using {len(local_glossary)} local glossary terms for {filename}")

    # Lock that serialises checkpoint saves when entries run concurrently.
    save_lock = threading.Lock()

    def save_current_state() -> None:
        """Write the current in-memory data back to disk immediately."""
        try:
            toml_text = toml.dumps(data)
            toml_text = reorder_glossary_blocks(toml_text)
            toml_text = reorder_language_fields(toml_text, target_languages)
            with open(toml_path, 'w', encoding='utf-8', newline='') as f:
                f.write(toml_text)
            logger.debug(f"Checkpoint save: {filename}")
        except Exception as e:
            logger.error(f"Failed checkpoint save of {filename}: {e}")

    translations_made = 0
    entries_processed = 0
    modified = False

    # ------------------------------------------------------------------ #
    # Inner helper: translate one entry and apply the result.             #
    # Returns (translations_delta, was_modified).                         #
    # ------------------------------------------------------------------ #
    def _process_entry(key: str, entry: dict) -> Tuple[int, bool]:
        """Translate a single entry for all required languages.

        Thread-safe: reads ``data`` in-place (entry is a reference from
        ``data``), and serialises writes through ``save_lock``.
        """
        if _work_tracker:
            _work_tracker.record_start("entry", f"{filename}/{key}")

        has_new_field = "new" in entry
        raw_text = entry.get("raw", "")

        missing_languages = [
            lang for lang in target_languages
            if (lang not in entry or not entry.get(lang)) and raw_text and raw_text.strip()
        ]

        if not has_new_field and not missing_languages:
            if _work_tracker:
                _work_tracker.record_end("entry", f"{filename}/{key}")
            return 0, False

        if has_new_field:
            logger.info(f"Processing entry with 'new' field: {key}")
            languages_to_translate = target_languages
        else:
            logger.info(f"Processing entry with missing translations: {key} (missing: {', '.join(missing_languages)})")
            languages_to_translate = missing_languages

        all_translations_successful = True
        new_translations: dict = {}

        # Parallelise over languages within this entry
        lang_cap = len(languages_to_translate) if max_lang_threads <= 0 else min(len(languages_to_translate), max_lang_threads)
        with ThreadPoolExecutor(max_workers=lang_cap) as lang_executor:
            future_to_lang = {
                lang_executor.submit(
                    translate_entry,
                    translator=translator,
                    key=key,
                    entry=entry,
                    target_lang=lang,
                    mod_name=mod_name,
                    prompt=prompt,
                    glossary=merged_glossary,
                    language_priority=target_languages
                ): lang
                for lang in languages_to_translate
            }

            for future in as_completed(future_to_lang):
                lang = future_to_lang[future]
                try:
                    translation = future.result()
                    if translation is not None:
                        new_translations[lang] = translation
                    else:
                        logger.warning(f"Failed to translate {key} to {lang}")
                        all_translations_successful = False
                except Exception as e:
                    logger.error(f"Error translating {key} to {lang}: {e}")
                    all_translations_successful = False

        if not new_translations or dry_run:
            if _work_tracker:
                _work_tracker.record_end("entry", f"{filename}/{key}")
            return len(new_translations), False

        # --- critical section: mutate shared ``data`` and persist --- #
        with save_lock:
            if has_new_field and all_translations_successful:
                entry["raw"] = entry["new"]

            for lang, translation in new_translations.items():
                entry[lang] = translation

            if has_new_field and all_translations_successful:
                del entry["new"]
                logger.info(f"Completed translation for {key}, removed 'new' field")

            # Persist immediately so a mid-run cancellation loses at most one entry.
            save_current_state()

        if _work_tracker:
            _work_tracker.record_end("entry", f"{filename}/{key}")
        return len(new_translations), True

    # ------------------------------------------------------------------ #
    # Collect entries that need work, then dispatch.                      #
    # ------------------------------------------------------------------ #
    pending_entries = [
        (key, entry)
        for key, entry in data.items()
        if key not in ("name", "prompt", "_meta") and isinstance(entry, dict)
    ]

    # Resolve the thread count: supports both a plain int and a callable so
    # that callers can pass a live counter that re-evaluates as siblings finish.
    def _resolve_threads() -> int:
        if callable(max_entry_threads):
            return max_entry_threads()
        return max_entry_threads

    current_threads = _resolve_threads()
    if current_threads > 1:
        # Sliding-window parallel entry processing.
        #
        # Instead of creating a pool with a fixed max_workers and submitting
        # every entry up-front, we maintain a live "window" of at-most
        # _resolve_threads() concurrent futures.  After each completion we
        # re-check _resolve_threads() and top up to the new limit.  This means
        # a file that started with 2 entry-threads will automatically scale up
        # to 10 as sibling files finish, without needing to recreate the pool.
        with ThreadPoolExecutor(max_workers=50) as entry_executor:
            pending_queue = list(pending_entries)
            in_flight: dict = {}  # future -> key

            def _fill_window():
                target = _resolve_threads()
                while pending_queue and len(in_flight) < target:
                    key, entry = pending_queue.pop(0)
                    fut = entry_executor.submit(_process_entry, key, entry)
                    in_flight[fut] = key
                if in_flight:
                    logger.info(
                        f"[threads] {filename}: window={len(in_flight)}, "
                        f"target={target}, remaining={len(pending_queue)}"
                    )

            _fill_window()
            while in_flight:
                done, _ = fut_wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    key = in_flight.pop(future)
                    try:
                        delta, entry_modified = future.result()
                        if delta > 0:
                            entries_processed += 1
                            translations_made += delta
                        if entry_modified:
                            modified = True
                    except Exception as e:
                        logger.error(f"Error processing entry {key}: {e}")
                _fill_window()
    else:
        # Sequential entry processing (original behaviour)
        for key, entry in pending_entries:
            try:
                delta, entry_modified = _process_entry(key, entry)
                if delta > 0:
                    entries_processed += 1
                    translations_made += delta
                if entry_modified:
                    modified = True
            except Exception as e:
                logger.error(f"Error processing entry {key}: {e}")

    if modified and not dry_run:
        logger.info(f"Saved updates to {filename}")

    return translations_made, entries_processed


def reformat_all_files(
    data_dir: str,
    target_languages: List[str],
    max_threads: int = 1,
    force_rewrite: bool = False
) -> None:
    """
    Reformat and reorganize all TOML files without translation
    
    Args:
        data_dir: Directory containing TOML files
        target_languages: List of target language codes for ordering
        max_threads: Maximum number of concurrent file processing threads
        force_rewrite: Force rewrite all files even if they seem correctly ordered
    """
    logger = logging.getLogger("reformat_all_files")
    
    # Find all TOML files
    toml_files = []
    for filename in os.listdir(data_dir):
        if filename.endswith('.toml') and (
            not filename.startswith('_') or filename.startswith('_ingame')
        ):
            toml_files.append(filename)
    
    if not toml_files:
        logger.warning(f"No TOML files found in {data_dir}")
        return
    
    logger.info(f"Found {len(toml_files)} TOML files to check")
    
    files_reformatted = 0
    files_skipped = 0
    
    # Process files (single-threaded for simplicity)
    for filename in toml_files:
        toml_path = os.path.join(data_dir, filename)
        
        try:
            # Read original file content as text first
            with open(toml_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
            
            # Apply reordering to text (without toml.load/dumps cycle)
            reordered_content = reorder_glossary_blocks(original_content)
            reordered_content = reorder_language_fields(reordered_content, target_languages)
            
            # Only write if content actually changed or if forced
            if reordered_content != original_content or force_rewrite:
                with open(toml_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(reordered_content)
                logger.info(f"Reformatted {filename}")
                files_reformatted += 1
            else:
                logger.debug(f"Skipped {filename} (already correctly ordered)")
                files_skipped += 1
            
        except Exception as e:
            logger.error(f"Failed to reformat {filename}: {e}")
    
    logger.info(f"Reformat complete! {files_reformatted} files reformatted, {files_skipped} files skipped")


def process_all_files(
    data_dir: str,
    translator: TranslatorLLM,
    target_languages: List[str],
    ingame_languages: Optional[List[str]] = None,
    max_threads: int = 1,
    max_threads_per_file: int = 10,
    dry_run: bool = False,
    max_time: Optional[int] = None
) -> None:
    """
    Process all TOML files in the data directory.

    Thread allocation strategy (OPTIMIZED)
    --------------------------------------
    **Simplified single-level concurrency** to avoid over-parallelization:

    1. **File level** – up to ``min(num_files, max_threads)`` files run in
       parallel.
    2. **Entry level** – **SERIALIZED** within each file.  Entries are processed
       one at a time to reduce thread contention and rate-limit queue pressure.
    3. **Language level** – within each entry, all target languages are translated
       in parallel (up to ``min(num_languages, max_threads_per_file)`` threads).

    This model prevents the "thundering herd" problem where hundreds of threads
    compete for rate-limited API calls, causing timeouts and slowdowns.

    Example with ``max_threads=10``:
    - 10 files active → 1 file per thread, entries serialized, languages parallel
    - 5 files active → same, but more CPU headroom per file
    - 1 file  active → full resources for language-level parallelism

    Args:
        data_dir: Directory containing TOML files
        translator: Translator instance
        target_languages: List of target language codes
        max_threads: Total concurrency budget for file-level parallelism.
            Each file processes entries sequentially but translates languages in parallel.
        max_threads_per_file: Upper bound on language-level threads per entry.
            Controls how many languages a single entry may translate concurrently.
        dry_run: If True, don't write changes
        max_time: Maximum processing time in seconds.
    """
    logger = logging.getLogger("process_all_files")

    # Initialise the global thread work tracker so all downstream functions
    # record their work assignments.
    global _work_tracker
    _work_tracker = ThreadWorkTracker()
    
    start_time = time.time()
    
    # Find all TOML files.
    # Include _ingame*.toml files (ingame localization for unsupported languages).
    # Exclude all other _-prefixed files (e.g. _glossary.toml).
    all_toml_files = list(Path(data_dir).glob("*.toml"))
    toml_files = [
        f for f in all_toml_files
        if not f.name.startswith('_') or f.name.startswith('_ingame')
    ]
    excluded_count = len(all_toml_files) - len(toml_files)
    logger.info(f"Found {len(toml_files)} TOML files to process (excluded {excluded_count} system files)")
    
    if not toml_files:
        logger.warning("No TOML files found")
        return

    # ------------------------------------------------------------------
    # Compute initial file-level thread count
    # ------------------------------------------------------------------
    active_file_threads = min(len(toml_files), max_threads)

    # ------------------------------------------------------------------
    # Optimized thread allocation: file-level parallelism only
    # ------------------------------------------------------------------
    # Each file processes entries sequentially, but translates all languages
    # within an entry in parallel. This prevents over-parallelization where
    # hundreds of threads compete for rate-limited API calls.
    
    def _process_file_wrapper(toml_file) -> Tuple[int, int]:
        """Wrapper that processes a single file with serialized entries."""
        if _work_tracker:
            _work_tracker.record_start(
                "file", toml_file.name,
                detail=f"entry_threads=1 (serialized), language_threads={max_threads_per_file}"
            )

        # Ingame files (_ingame*.toml) are translated only for languages that are
        # supported by this project but not natively supported by the game.
        is_ingame = toml_file.name.startswith('_ingame')
        file_languages = (ingame_languages if (is_ingame and ingame_languages)
                          else target_languages)
        if is_ingame:
            logger.info(
                f"[ingame] {toml_file.name}: using ingame-only languages: "
                f"{', '.join(file_languages)}"
            )

        logger.info(
            f"[threads] Start {toml_file.name}: "
            f"entries=sequential, language_threads={max_threads_per_file}"
        )
        try:
            # Pass 1 for entry-level threads (serialized), max_threads_per_file for language-level
            return process_toml_file(
                str(toml_file),
                translator,
                file_languages,
                dry_run,
                max_entry_threads=1,  # Serialize entries within each file
                max_lang_threads=max_threads_per_file,
            )
        finally:
            if _work_tracker:
                _work_tracker.record_end("file", toml_file.name)
            logger.info(
                f"[threads] Done  {toml_file.name}"
            )

    logger.info(
        f"Thread allocation (OPTIMIZED): up to {active_file_threads} concurrent file(s), "
        f"entries processed sequentially within each file, "
        f"languages translated in parallel (max {max_threads_per_file} per entry)"
    )

    total_translations = 0
    total_entries = 0
    
    if active_file_threads > 1:
        # Process files in parallel
        with ThreadPoolExecutor(max_workers=active_file_threads) as executor:
            futures = {
                executor.submit(_process_file_wrapper, toml_file): toml_file
                for toml_file in toml_files
            }
            
            for future in as_completed(futures):
                # Check timeout before processing result
                if max_time and (time.time() - start_time) >= max_time:
                    logger.warning(f"Time limit of {max_time} seconds reached. Stopping processing of new files.")
                    # Cancel remaining futures
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break
                
                toml_file = futures[future]
                try:
                    translations, entries = future.result()
                    total_translations += translations
                    total_entries += entries
                except Exception as e:
                    logger.error(f"Error processing {toml_file}: {e}")
    else:
        # Single file at a time: process sequentially at file level but use language-level parallelism
        logger.info("Processing files sequentially (language-level parallelism active)")
        for toml_file in toml_files:
            # Check timeout before processing each file
            if max_time and (time.time() - start_time) >= max_time:
                logger.warning(f"Time limit of {max_time} seconds reached. Stopping processing of new files.")
                logger.info(f"Processed {len(toml_files) - toml_files.index(toml_file)} of {len(toml_files)} files before timeout")
                break
            
            try:
                translations, entries = _process_file_wrapper(toml_file)
                total_translations += translations
                total_entries += entries
            except Exception as e:
                logger.error(f"Error processing {toml_file}: {e}")
    
    logger.info(f"Translation complete!")
    logger.info(f"Processed {total_entries} entries")
    logger.info(f"Made {total_translations} translations")

    # Emit the full thread work allocation summary
    if _work_tracker:
        summary = _work_tracker.summary()
        for line in summary.splitlines():
            logger.info(line)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Automated translation for Timberborn mods"
    )
    parser.add_argument(
        "--config",
        default=".github/config/config.toml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing TOML files"
    )
    parser.add_argument(
        "--api-token",
        help="API token for LLM (overrides secrets.LLM_TOKEN)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making changes"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (overrides config log level)"
    )
    parser.add_argument(
        "--log-file",
        help="Path to log file (default: no file output, only console)"
    )
    parser.add_argument(
        "--max-time",
        type=int,
        help="Maximum processing time in seconds. Script will stop taking new entries when time is up."
    )
    parser.add_argument(
        "--glossary",
        default="data/_glossary.toml",
        help="Path to glossary file (default: data/_glossary.toml)"
    )
    parser.add_argument(
        "--reformat-only",
        action="store_true",
        help="Only reformat/reorganize TOML files without translation"
    )
    parser.add_argument(
        "--thread-log-file",
        help="Path to dedicated thread-allocation / work-info log file. "
             "Captures all thread lifecycle events at DEBUG level regardless "
             "of the main log level."
    )
    
    args = parser.parse_args()

    # Setup a bootstrap logger before config load so fatal errors are always loggable
    setup_logging("INFO")
    logger = logging.getLogger("main")

    try:
        # Load configuration first
        config = load_config(args.config)
        
        # Load language names from config
        global LANGUAGE_NAMES
        LANGUAGE_NAMES = load_language_names_from_config(config)
        
        # Load glossary
        global GLOSSARY
        glossary_path = args.glossary
        GLOSSARY = load_glossary(glossary_path)
        
        # Setup logging with config-based or command-line level
        log_config = config.get("logging", {})
        if args.verbose:
            log_level = "DEBUG"
        else:
            log_level = log_config.get("level", "INFO")

        # Resolve thread log file: CLI arg > config > None
        thread_log_file = getattr(args, 'thread_log_file', None) or log_config.get("thread_log_file")
        
        setup_logging(log_level, thread_log_file=thread_log_file)
        logger = logging.getLogger("main")
        
        # Add file handler if log file specified
        if args.log_file:
            file_handler = logging.FileHandler(args.log_file, mode='w', encoding='utf-8')
            file_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
            file_handler.setFormatter(
                logging.Formatter('%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
            )
            logging.getLogger().addHandler(file_handler)
            logger.info(f"Logging to file: {args.log_file}")
        
        logger.info("Loading configuration...")
        
        # Load target languages from config
        logger.info("Loading target languages...")
        target_languages = load_target_languages(config)
        
        # Check if this is reformat-only mode
        if args.reformat_only:
            logger.info("REFORMAT ONLY MODE - Only reorganizing files, no translation")
            reformat_all_files(
                data_dir=args.data_dir,
                target_languages=target_languages,
                max_threads=1  # Simple single-threaded for reformat
            )
            logger.info("All done!")
            return
        
        # For translation mode, need API token
        # Get API token
        api_token = args.api_token
        if not api_token:
            # Try environment variable first
            api_token = os.environ.get('LLM_TOKEN') or os.environ.get('OPENAI_API_KEY')
        
        if not api_token:
            # Try to get from secrets module (if it exists and has LLM_TOKEN attribute)
            try:
                import secrets as secrets_module
                if hasattr(secrets_module, 'LLM_TOKEN'):
                    api_token = secrets_module.LLM_TOKEN
            except (ImportError, AttributeError):
                pass
        
        if not api_token:
            logger.error("API token not found. Please provide via --api-token, secrets.LLM_TOKEN, or LLM_TOKEN/OPENAI_API_KEY environment variable")
            sys.exit(1)
        
        # Load target languages from config
        logger.info("Loading target languages...")
        target_languages = load_target_languages(config)

        # Compute ingame-only languages (supported ∖ game_supported)
        ingame_languages = load_ingame_languages(config)
        
        # Initialize translator
        logger.info("Initializing translator...")
        llm_config = config.get("llm", {})
        rate_config = config.get("rate_limiter", {})
        
        rpm = rate_config.get("max_requests_per_minute", 10)
        rate_limit = f"{rpm}/m"
        
        # Cost control settings
        max_cost = rate_config.get("max_cost_per_run", 0.0)
        cost_warning = rate_config.get("cost_warning_threshold", 1.0)
        
        if max_cost > 0:
            logger.info(f"Cost limit enabled: max ${max_cost:.2f} per run, warning at ${cost_warning:.2f}")
        
        translator = TranslatorLLM(
            api_token=api_token,
            model=llm_config.get("model", "gpt-6-luna"),
            reasoning_effort=llm_config.get("reasoning_effort", "none"),
            api_url=llm_config.get("api_url", "https://api.openai.com/v1/chat/completions"),
            min_length=llm_config.get("min_length", 1),
            max_length=llm_config.get("max_length", 5000),
            rate_limit=rate_limit,
            max_cost=max_cost,
            cost_warning_threshold=cost_warning
        )
        
        max_threads = rate_config.get("max_threads", 1)
        max_threads_per_file = rate_config.get("max_threads_per_file", 10)
        
        if args.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        if args.max_time:
            logger.info(f"Maximum processing time: {args.max_time} seconds")
        
        # Process all files with translation
        process_all_files(
            data_dir=args.data_dir,
            translator=translator,
            target_languages=target_languages,
            ingame_languages=ingame_languages,
            max_threads=max_threads,
            max_threads_per_file=max_threads_per_file,
            dry_run=args.dry_run,
            max_time=args.max_time
        )
        
        # Output cost summary
        logger.info(translator.get_cost_summary())
        
        # Write cost report to file for GitHub Actions summary
        cost_report_path = os.path.join(os.path.dirname(args.log_file) or '.', 'cost_report.json')
        cost_data = translator.get_cost_summary_dict()
        try:
            with open(cost_report_path, 'w', encoding='utf-8') as f:
                json.dump(cost_data, f, indent=2)
            logger.info(f"Cost report written to {cost_report_path}")
        except Exception as e:
            logger.error(f"Failed to write cost report: {e}")
        
        if cost_data["fail_count"] or not cost_data["cost_tracking_complete"]:
            raise RuntimeError("Translation requests failed; pending entries and partial progress have been preserved")
        logger.info("All done!")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
