# Data Structure Documentation

## Translation TOML File Structure

Each mod's translations are stored in a TOML file with the following structure:

If you don't know what a TOML file is, please take one minute to read this document: https://toml.io

### Basic Structure

```toml
# Metadata section for mod configuration
[_meta]
name = "Mod Name"
prompt = "Optional translation hints"

# Mod-local glossary
[_meta.glossary."CustomTerm"]
zhCN = "自定义术语"
zhTW = "自訂術語"

["Translation.Entry.Key"]
raw = "Original English text"
status = "normal"  # normal / old / abandon
new = "Updated text"  # If present, triggers retranslation
prompt = "Entry-specific translation hints"
enUS = "English translation"
zhCN = "Chinese (Simplified) translation"
zhTW = "Chinese (Traditional) translation"
ruRU = "Russian translation"
jaJP = "Japanese translation"
frFR = "French translation"
deDE = "German translation"
plPL = "Polish translation"
ptBR = "Portuguese (Brazil) translation"
koKR = "Korean translation"
```

### Field Descriptions

#### `[_meta]` Section

The `_meta` section contains metadata that doesn't conflict with translation keys:

- **name**: Mod name (required)
- **prompt**: Optional translation hints for the mod
- **glossary**: Mod-specific glossary entries

##### Glossary Structure

```toml
[_meta.glossary."Term"]
zhCN = "翻译"
zhTW = "翻譯"
# Only define languages you need

# Use 'all' keyword as fallback for all languages
[_meta.glossary."UniversalTerm"]
all = "通用术语"  # Used when specific language is not defined

# Advanced format with options
[_meta.glossary."AdvancedTerm"]
skip_hints = true  # Don't show hints for missing translations
fuzzy_tolerance = 3  # Custom tolerance for fuzzy matching
zhCN = "高级术语"
all = "高级术语"  # Fallback for other languages
```

###### 'all' Keyword Priority

When resolving translations, the system follows this priority order:
1. **Local (mod-specific) glossary with specific language** (e.g., `zhCN`)
2. **Local glossary with 'all' keyword**
3. **Global glossary with specific language**
4. **Global glossary with 'all' keyword**

Example:
```toml
# Global glossary (data/_glossary.toml)
["CommonTerm"]
all = "通用翻译"
zhCN = "简体翻译"

# Local glossary (mod TOML file)
[_meta.glossary."CommonTerm"]
all = "本地通用翻译"
# zhCN not defined

# Result for zhCN: "本地通用翻译" (local 'all' has higher priority than global zhCN)
# Result for zhTW: "本地通用翻译" (local 'all')
# Result for other languages: "本地通用翻译" (local 'all')
```

#### Translation Entry Fields

Each translation entry uses a key in the format `["Category.Item.Field"]`:

- **raw**: Original text (Most of time, it's English) from the mod
- **status**: Translation status
  - `normal`: Active translation
  - `old`: The mod retains this key value in older versions.
  - `abandon`: Abandoned/unused
- **new**: Updated text that triggers retranslation (removed after translation completes)
- **copy**: Boolean flag (default: false) - If true, treats this as a symbolic field and copies the source text directly without translation
- **prompt**: Entry-specific translation hints
- **Language codes**: Translations for each supported language

Hint: Most of time, you only need change prompt and translations.

### Supported Languages

The authoritative list is `[languages].supported` in `.github/config/config.toml`.

Official game languages:

- `enUS`: English (US)
- `zhCN`: Chinese (Simplified)
- `zhTW`: Chinese (Traditional)
- `ruRU`: Russian
- `jaJP`: Japanese
- `frFR`: French
- `deDE`: German
- `plPL`: Polish
- `ptBR`: Portuguese (Brazil)
- `koKR`: Korean
- `trTR`: Turkish
- `esES`: Spanish
- `itIT`: Italian
- `ukUA`: Ukrainian
- `thTH`: Thai

Languages the game does not ship. For these the base game text is translated as well (`data/_ingame*.toml`):

- `csCZ`: Czech
- `viVN`: Vietnamese
- `caES`: Catalan
- `beBY`: Belarusian
- `roRO`: Romanian
- `huHU`: Hungarian
- `zhHK`: Cantonese (Hong Kong)

### Example: Complete Mod File

```toml
name = "Building Expansion Mod"
prompt = "Building names and descriptions"

[_meta]
[_meta.glossary."CustomBuilding"]
zhCN = "自定义建筑"
zhTW = "自訂建築"

["Building.CustomBuilding.DisplayName"]
raw = "Custom Building"
status = "normal"
enUS = "Custom Building"
zhCN = "自定义建筑"
zhTW = "自訂建築"
ruRU = "Специальное здание"
jaJP = "カスタムビルディング"
frFR = "Bâtiment personnalisé"
deDE = "Benutzerdefiniertes Gebäude"
plPL = "Niestandardowy budynek"
ptBR = "Edifício Personalizado"
koKR = "사용자 지정 건물"

["Building.CustomBuilding.Description"]
raw = "A building with special features"
status = "normal"
new = "A building with amazing features"  # Triggers retranslation
prompt = "Keep translation concise"
enUS = "A building with special features"
zhCN = "具有特殊功能的建筑"
# ... other languages

["Building.CustomBuilding.Id"]
raw = "CustomBuilding_001"
status = "normal"
copy = true  # Symbolic field - copy directly without translation
enUS = "CustomBuilding_001"
zhCN = "CustomBuilding_001"  # Same as source
# ... other languages (all identical)
```

### Translation Workflow

1. **New mod added**: Only `new` field exists, no `raw` field yet
2. **First translation**: Script translates `new` to all languages
3. **After translation**: `raw` is set to `new` value, `new` field is removed
4. **Update detected**: If text changes, `new` field is added again
5. **Retranslation**: Script retranslates for all languages, updates `raw`, removes `new`

### Glossary Features

The glossary system supports:

- **Global glossary**: `data/_glossary.toml` in data directory
- **Mod-local glossary**: `[_meta.glossary]` in mod TOML file
- **Priority**: Local glossary overrides global glossary (with 'all' keyword support)
- **'all' keyword**: Universal fallback when specific language is not defined
- **Partial languages**: Only define languages you need
- **Fuzzy matching**: For terms 10+ characters with configurable tolerance
- **Skip hints**: Prevent hints for specific terms (proper names)
- **Language priority**: Hints show only first available language

See [README.md](../README.md) for more details on glossary features.


## Global Glossary System

The global glossary (`data/_glossary.toml`) provides translations for commonly used game-specific terms that appear across multiple mods. This reduces translation workload and ensures consistency.

### Key Features

#### Case-Insensitive Matching

The glossary system uses **case-insensitive matching**, meaning you only need to define one entry per term:

```toml
# This single entry matches all case variations:
["tank"]
zhCN = "水罐"
zhTW = "水罐"

# Matches: "tank", "Tank", "TANK", "tAnK", etc.
```

**Benefits:**
- No need for duplicate entries like `["tank"]` and `["Tank"]
- Simplified maintenance
- Consistent translations across case variations

#### Fuzzy Matching

For terms with 10+ characters, the system allows character differences (default: 2 characters):

```toml
["Construction"]
zhCN = "建设"
# Also matches: "Consruction" (missing 't'), "Constructoin" (transposed 'io')

# Custom tolerance
["Infrastructure"]
fuzzy_tolerance = 3
zhCN = "基础设施"
# Allows up to 3 character differences
```

#### Special Options

```toml
# Skip translation hints (for proper names)
["Timberborn"]
skip_hints = true
zhCN = "海狸浮生记"
zhTW = "海狸浮生記"

# Custom fuzzy tolerance
["VeryLongTermName"]
fuzzy_tolerance = 4
zhCN = "很长的术语名称"

# Use 'all' keyword as universal fallback
["UniversalTerm"]
all = "通用翻译"
zhCN = "简体翻译"  # Overrides 'all' for zhCN

# New format with explicit structure (optional)
["ModernTerm"]
skip_hints = false
fuzzy_tolerance = 2
translations = { zhCN = "现代术语", zhTW = "現代術語" }
```

### Priority System

The glossary lookup follows this priority order:

1. **Local (mod-specific) glossary with specific language** - highest priority
2. **Local glossary with 'all' keyword** - universal fallback for local glossary
3. **Global glossary with specific language** - global translation for specific language
4. **Global glossary with 'all' keyword** - universal fallback for global glossary

**Example:**
```toml
# Global glossary (data/_glossary.toml)
["Building"]
all = "建筑物"     # Used by all languages if not overridden
zhCN = "建筑"      # Specific for Simplified Chinese

# Mod-local glossary in mod TOML
[_meta.glossary."Building"]
all = "构建物"     # Overrides global for this mod
zhTW = "建築"      # Specific for Traditional Chinese

# Results for this mod:
# - zhTW: "建築" (local specific)
# - zhCN: "构建物" (local 'all', higher priority than global zhCN)
# - Other languages: "构建物" (local 'all')
```

### Usage Examples

#### Before Translation (Source Text)
```
"The Tank needs maintenance"
```

#### After Glossary Processing
```
"The 水罐 needs maintenance"  # zhCN
"The 水罐 needs maintenance"  # zhTW
```

#### Translation Hints
When a glossary term exists but lacks the target language:
```
Term "pump" in zhCN: 水泵
```
