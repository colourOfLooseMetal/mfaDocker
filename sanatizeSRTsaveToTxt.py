import pysrt
import os
import re
from num2words import num2words

FOLDER_PATH = r"./Seinfeld/Season1"  # <-- change this
TEST_MODE = False  # <-- set to False to actually write .txt files

# --- Number conversion helpers ---

def num_words(n, ordinal=False):
    mode = 'ordinal' if ordinal else 'cardinal'
    return num2words(int(n), to=mode).replace('-', ' ').replace(',', '')

def hundreds_form(n):
    """Return 'X hundred' form for 1100-9900 multiples of 100 (excluding multiples of 1000), else None."""
    if 1100 <= n <= 9900 and n % 100 == 0 and n % 1000 != 0:
        return f"{num_words(n // 100)} hundred"
    return None

def pretty_num(n):
    """Use 'X hundred' form if applicable, otherwise standard cardinal words."""
    h = hundreds_form(n)
    return h if h else num_words(n)

def time_to_words(match):
    hour, minute = int(match.group(1)), int(match.group(2))
    h = num_words(hour)
    if minute == 0:
        return h
    if minute < 10:
        return f"{h} oh {num_words(minute)}"
    return f"{h} {num_words(minute)}"

def phone_to_words(match):
    digits = re.sub(r'\D', '', match.group(0))
    return ' '.join(num_words(d) for d in digits)

def currency_to_words(match):
    raw = match.group(1).replace(',', '')
    if '.' in raw:
        d_str, c_str = raw.split('.')
        dollars = int(d_str) if d_str else 0
        cents = int((c_str + '00')[:2])
        parts = []
        if dollars:
            parts.append(f"{pretty_num(dollars)} dollar{'s' if dollars != 1 else ''}")
        if cents:
            parts.append(f"{num_words(cents)} cent{'s' if cents != 1 else ''}")
        return ' and '.join(parts) if parts else 'zero dollars'
    n = int(raw)
    return f"{pretty_num(n)} dollar{'s' if n != 1 else ''}"

def ordinal_to_words(match):
    return num_words(int(match.group(1)), ordinal=True)

def plain_num_to_words(match):
    return pretty_num(int(match.group(0).replace(',', '')))

def convert_numbers(text):
    # ORDER MATTERS — most specific patterns first
    text = re.sub(r'\b(\d{1,2}):(\d{2})\b', time_to_words, text)
    text = re.sub(r'\b\d+(?:-\d+)+\b', phone_to_words, text)
    # FIXED: first alt now REQUIRES a comma, so $1500 falls through to second alt
    text = re.sub(r'\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)', currency_to_words, text)
    text = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', ordinal_to_words, text, flags=re.IGNORECASE)
    text = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', plain_num_to_words, text)
    text = re.sub(r'\b\d+\b', plain_num_to_words, text)
    return text

# --- Text cleaning ---

def clean_text(text):
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'♪[^♪]*♪', '', text)
    text = re.sub(r'♪.*', '', text)
    text = re.sub(r'^[A-Z][A-Z\s]+:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'--+', '', text)
    text = re.sub(r'^\s*-\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = text.strip()
    if re.fullmatch(r'[\s\W]+', text):
        return ''
    return text

# --- Main ---

def process_srts(folder_path, test_mode=False):
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    srt_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".srt")]
    if not srt_files:
        print("No .srt files found in the folder.")
        return

    mode_label = "TEST MODE (no files written)" if test_mode else "WRITE MODE"
    print(f"=== {mode_label} ===")
    print(f"Found {len(srt_files)} .srt file(s).\n")

    success, skipped, failed = 0, 0, 0
    total_number_lines = 0

    for filename in srt_files:
        input_path = os.path.join(folder_path, filename)
        output_path = os.path.join(folder_path, os.path.splitext(filename)[0] + ".txt")

        if not test_mode and os.path.exists(output_path):
            print(f"[SKIP]    {filename} → .txt already exists.")
            skipped += 1
            continue

        try:
            subs = pysrt.open(input_path, error_handling=pysrt.ERROR_PASS)
            lines = []
            file_number_lines = []

            for sub in subs:
                cleaned = clean_text(sub.text)
                if not cleaned:
                    continue
                converted = convert_numbers(cleaned)
                if re.search(r'\d', cleaned):
                    file_number_lines.append((cleaned, converted))
                lines.append(converted)

            if test_mode:
                if file_number_lines:
                    print(f"\n──── {filename} ──── ({len(file_number_lines)} line(s) with numbers)")
                    for original, converted in file_number_lines:
                        print(f"  BEFORE: {original}")
                        print(f"  AFTER : {converted}")
                        print()
                    total_number_lines += len(file_number_lines)
                else:
                    print(f"──── {filename} ──── (no numeric content)")
                success += 1
            else:
                full_text = " ".join(lines)
                full_text = re.sub(r'\s{2,}', ' ', full_text).strip()
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(full_text)
                print(f"[OK]      {filename} → {os.path.basename(output_path)}")
                success += 1

        except Exception as e:
            print(f"[FAIL]    {filename} — error: {e}")
            failed += 1

    print("\n" + "=" * 50)
    if test_mode:
        print(f"Test complete. {success} file(s) scanned, {total_number_lines} total line(s) with numbers, {failed} failed.")
        print("No files were written. Set TEST_MODE = False to write .txt files.")
    else:
        print(f"Done. ✓ {success} processed  ⊘ {skipped} skipped  ✗ {failed} failed.")


process_srts(FOLDER_PATH, test_mode=TEST_MODE)