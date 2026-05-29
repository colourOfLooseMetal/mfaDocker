import pysrt
import os
import re

FOLDER_PATH = r"./Seinfeld/Season1"  # <-- change this

def num_there(s):
    return any(i.isdigit() for i in s)

def extract_text_from_srts(folder_path):
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    srt_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".srt")]

    if not srt_files:
        print("No .srt files found in the folder.")
        return

    print(f"Found {len(srt_files)} .srt file(s). Extracting text...\n")

    success, skipped, failed = 0, 0, 0
    lines = []
    for filename in srt_files:
        input_path = os.path.join(folder_path, filename)
        output_path = os.path.join(folder_path, os.path.splitext(filename)[0] + "nums.txt")

        if os.path.exists(output_path):
            print(f"[SKIP]    {filename} → .txt already exists.")
            skipped += 1
            continue

        try:
            subs = pysrt.open(input_path, error_handling=pysrt.ERROR_PASS)


            for sub in subs:
                if num_there(sub.text):
                    lines.append(sub.text)



        except Exception as e:
            print(f"[FAIL]    {filename} — error: {e}")
            failed += 1
    full_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    print(f"[OK]      {filename} → {os.path.basename(output_path)}")
extract_text_from_srts(FOLDER_PATH)