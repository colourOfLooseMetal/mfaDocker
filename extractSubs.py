import ffmpeg
import os

FOLDER_PATH = r"./Seinfeld/Season9"  # <-- change this

def extract_subtitles(folder_path):
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    mkv_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".mkv")]

    if not mkv_files:
        print("No .mkv files found in the folder.")
        return

    print(f"Found {len(mkv_files)} .mkv file(s). Extracting subtitles...\n")

    success, skipped, failed = 0, 0, 0

    for filename in mkv_files:
        input_path = os.path.join(folder_path, filename)
        output_path = os.path.join(folder_path, os.path.splitext(filename)[0] + ".srt")

        if os.path.exists(output_path):
            print(f"[SKIP]    {filename} → .srt already exists.")
            skipped += 1
            continue

        try:
            (
                ffmpeg
                .input(input_path)
                .output(output_path, map="0:s:0", c="srt")
                .overwrite_output()
                .run(quiet=True)
            )
            print(f"[OK]      {filename} → {os.path.basename(output_path)}")
            success += 1

        except ffmpeg.Error as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "No details."
            if "matches no streams" in stderr or "Invalid option" in stderr:
                print(f"[NO SUBS] {filename} — no subtitle stream found.")
            else:
                print(f"[FAIL]    {filename} — ffmpeg error:\n          {stderr.strip()}")
            failed += 1

    print(f"\nDone. ✓ {success} extracted  ⊘ {skipped} skipped  ✗ {failed} failed.")


extract_subtitles(FOLDER_PATH)