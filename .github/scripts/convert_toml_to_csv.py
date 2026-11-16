#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: (ALE-1.1 AND GPL-3.0-only)
# Copyright (c) 2022-2025 wuyilingwei
#
# This file is licensed under the ANTI-LABOR EXPLOITATION LICENSE 1.1
# in combination with GNU General Public License v3.0.
# See .github/LICENSE for full license text.

import os
import re
import toml
import csv
import shutil
import sys
import logging
from pathlib import Path

from util.display_text import display_text


def setup_logger():
    """Setup logger with console and file handlers"""
    logger = logging.getLogger('toml_converter')
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Console handler for all messages
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler for warnings and errors only
    warnings_errors_file = 'conversion_warnings_errors.log'
    file_handler = logging.FileHandler(warnings_errors_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.WARNING)
    file_formatter = logging.Formatter('[%(levelname)s] %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger, warnings_errors_file


def load_config(config_path=".github/config/config.toml", logger=None):
    """Load configuration from TOML file"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return toml.load(f)
    except FileNotFoundError:
        if logger:
            logger.error(f"Configuration file not found: {config_path}")
        else:
            print(f"[ERROR] Configuration file not found: {config_path}")
        return {}
    except Exception as e:
        if logger:
            logger.error(f"Failed to load configuration: {e}")
        else:
            print(f"[ERROR] Failed to load configuration: {e}")
        return {}


def load_protected_fields_from_csv(csv_path, logger=None):
    """Load protected field IDs from CSV file"""
    protected_fields = set()
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'ID' in row and row['ID'].strip():
                    protected_fields.add(row['ID'].strip())
        if logger:
            logger.info(f"Loaded {len(protected_fields)} protected fields from {csv_path}")
        else:
            print(f"[Info] Loaded {len(protected_fields)} protected fields from {csv_path}")
    except FileNotFoundError:
        if logger:
            logger.warning(f"Protected strings file not found: {csv_path}")
        else:
            print(f"[WARNING] Protected strings file not found: {csv_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to load protected fields from {csv_path}: {e}")
        else:
            print(f"[ERROR] Failed to load protected fields from {csv_path}: {e}")
    return protected_fields


def get_protected_fields(config, logger=None):
    """Get all protected fields from config and CSV file"""
    protected_fields = set()
    
    # Get build config
    build_config = config.get("build", {})
    
    # Get extra protected string IDs from config
    extra_protect_ids = build_config.get("extra_protect_string_ids", [])
    protected_fields.update(extra_protect_ids)
    
    # Load protected fields from CSV file
    protect_file = build_config.get("protect_string_ids_file")
    if protect_file:
        csv_path = f".github/data/raw/{protect_file}"
        csv_protected_fields = load_protected_fields_from_csv(csv_path, logger)
        protected_fields.update(csv_protected_fields)
    
    if logger:
        logger.info(f"Total protected fields: {len(protected_fields)}")
    else:
        print(f"[Info] Total protected fields: {len(protected_fields)}")
    return protected_fields


def get_supported_languages(config, logger=None):
    """Get supported languages from config"""
    languages = config.get("languages", {}).get("supported", [])
    if not languages:
        if logger:
            logger.warning("No supported languages found in config, using fallback list")
        else:
            print("[WARNING] No supported languages found in config, using fallback list")
        languages = ["enUS", "zhCN", "zhTW", "ruRU", "jaJP", "frFR", "deDE", "plPL", "ptBR", "koKR"]
    
    if logger:
        logger.info(f"Using languages: {', '.join(languages)}")
    else:
        print(f"[Info] Using languages: {', '.join(languages)}")
    return set(languages)


def get_ingame_languages(config, logger=None):
    """
    Return languages in supported but NOT in game_supported.
    These are the only languages present in _ingame*.toml files.
    """
    supported = config.get("languages", {}).get("supported", [])
    game_supported = set(config.get("languages", {}).get("game_supported", []))
    ingame_only = [lang for lang in supported if lang not in game_supported]
    if logger:
        logger.info(f"Ingame-only languages: {', '.join(ingame_only)}")
    else:
        print(f"[Info] Ingame-only languages: {', '.join(ingame_only)}")
    return set(ingame_only)


# Mapping: _ingame*.toml stem → CSV suffix used in output filenames
INGAME_TOML_STEMS = {
    "_ingame":       "_ingame",
    "_ingame_des":   "_ingame_des",
}


def convert_toml_to_csv(data_dir, mod_dir, config_path=".github/config/config.toml"):
    """将TOML文件转换为CSV文件"""
    
    # Setup logger
    logger, warnings_errors_file = setup_logger()
    
    # Load configuration
    config = load_config(config_path, logger)
    if not config:
        logger.error("Failed to load configuration, exiting")
        return warnings_errors_file
    
    protected_fields = get_protected_fields(config, logger)
    supported_languages = get_supported_languages(config, logger)
    ingame_languages = get_ingame_languages(config, logger)

    for file_name in os.listdir(data_dir):
        if file_name.endswith(".toml"):
            # 忽略术语表文件
            if file_name == "_glossary.toml":
                continue

            # Handle _ingame*.toml files (ingame localization for game-unsupported languages)
            file_stem = file_name[:-5]
            if file_stem in INGAME_TOML_STEMS:
                csv_suffix = INGAME_TOML_STEMS[file_stem]
                toml_path = os.path.join(data_dir, file_name)
                output_dir = os.path.join(mod_dir, "Localizations")
                os.makedirs(output_dir, exist_ok=True)
                try:
                    with open(toml_path, "r", encoding="utf-8") as toml_file:
                        data = toml.load(toml_file)
                    for lang_code in ingame_languages:
                        csv_file_name = f"{lang_code}{csv_suffix}.csv"
                        csv_path = os.path.join(output_dir, csv_file_name)
                        empty_count = 0
                        with open(csv_path, "w", encoding="utf-8", newline="") as csv_file:
                            writer = csv.writer(csv_file)
                            writer.writerow(["ID", "Text", "Comment"])
                            for translation_key, translations in data.items():
                                if translation_key in ("name", "prompt", "_meta"):
                                    continue
                                if isinstance(translations, dict) and lang_code in translations:
                                    translation_text = translations[lang_code]
                                    if not translation_text or not translation_text.strip():
                                        empty_count += 1
                                        logger.info(f"Empty ingame translation for key '{translation_key}' in '{lang_code}' ({file_name})")
                                    else:
                                        processed_text = display_text(translation_text, lang_code)
                                        if translation_text.startswith(' '):
                                            processed_text = f'"{processed_text}"'
                                        writer.writerow([translation_key, processed_text, "-"])
                        if empty_count > 0:
                            logger.info(f"Skipped {empty_count} empty ingame entries for {lang_code} in {file_name}")
                        logger.info(f"Generated ingame CSV: {csv_file_name}")
                except Exception as e:
                    logger.error(f"Failed to process ingame file {toml_path}: {e}")
                continue

            # 提取 mod_id（不再区分版本）
            match = re.search(r"(\d+)\.toml", file_name)
            if not match:
                logger.warning(f"Skipping file {file_name}: does not match expected pattern")
                continue
            mod_id = match.group(1)
            
            toml_path = os.path.join(data_dir, file_name)
            output_dir = os.path.join(mod_dir, "Localizations")
            os.makedirs(output_dir, exist_ok=True)

            # 读取 TOML 文件
            try:
                with open(toml_path, "r", encoding="utf-8") as toml_file:
                    data = toml.load(toml_file)
                
                # 使用配置中的支持语言列表，并检查TOML中哪些语言有翻译
                available_languages = set()
                for key, translations in data.items():
                    # Skip metadata sections
                    if key in ["name", "prompt", "_meta"]:  # Changed field_prompt to prompt
                        continue
                    if isinstance(translations, dict):
                        for lang_code in translations.keys():
                            if lang_code in supported_languages:
                                available_languages.add(lang_code)
                
                # 只为有翻译内容的语言生成CSV文件
                all_languages = available_languages
                
                if not all_languages:
                    logger.info(f"No supported language translations found in {file_name}")
                    continue
                
                # 为每种语言生成 CSV 文件
                generated_files = []
                for lang_code in all_languages:
                    csv_file_name = f"{lang_code}_{mod_id}.csv"
                    csv_path = os.path.join(output_dir, csv_file_name)
                    
                    empty_entries_count = 0
                    protected_entries_count = 0
                    
                    with open(csv_path, "w", encoding="utf-8", newline="") as csv_file:
                        writer = csv.writer(csv_file)
                        writer.writerow(["ID", "Text", "Comment"])
                        
                        # 遍历所有翻译条目
                        for translation_key, translations in data.items():
                            # Skip metadata sections
                            if translation_key in ["name", "prompt", "_meta"]:  # Changed field_prompt to prompt
                                continue
                            
                            # Skip protected fields
                            if translation_key in protected_fields:
                                protected_entries_count += 1
                                logger.info(f"Skipped protected field '{translation_key}' in language '{lang_code}' (file: {file_name})")
                                continue
                            
                            if isinstance(translations, dict) and lang_code in translations:
                                translation_text = translations[lang_code]
                                # 检查空字符串并输出警告
                                if not translation_text or not translation_text.strip():
                                    empty_entries_count += 1
                                    logger.info(f"Empty translation for key '{translation_key}' in language '{lang_code}' (file: {file_name})")
                                else:
                                    # 如果文本以空格开头，为其添加双引号以防止游戏忽略前导空格
                                    processed_text = display_text(translation_text, lang_code)
                                    if translation_text.startswith(' '):
                                        processed_text = f'"{processed_text}"'
                                        logger.info(f"Added quotes to preserve leading space for key '{translation_key}' in language '{lang_code}' (file: {file_name})")
                                    
                                    writer.writerow([translation_key, processed_text, "-"])
                    
                    if empty_entries_count > 0:
                        logger.info(f"Skipped {empty_entries_count} empty entries for {lang_code} in {file_name}")
                    
                    if protected_entries_count > 0:
                        logger.info(f"Skipped {protected_entries_count} protected fields for {lang_code} in {file_name}")
                    
                    generated_files.append((csv_file_name, csv_path))
                        
            except Exception as e:
                logger.error(f"Failed to process {toml_path}: {e}")
    
    return warnings_errors_file


if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: convert_toml_to_csv.py <data_dir> <mod_dir> [config_path]")
        sys.exit(1)
    
    data_dir = sys.argv[1]
    mod_dir = sys.argv[2]
    config_path = sys.argv[3] if len(sys.argv) > 3 else ".github/config/config.toml"
    
    warnings_errors_file = convert_toml_to_csv(data_dir, mod_dir, config_path)
    
    # Print the warnings/errors file path for the workflow to use
    print(f"WARNINGS_ERRORS_FILE={warnings_errors_file}")
