import ffmpeg
import os

SEASONS = range(1, 10)  # Season1 .. Season9

def extract_audio(folder_path):
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    mkv_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".mkv")]

    if not mkv_files:
        print("No .mkv files found in the folder.")
        return

    print(f"Found {len(mkv_files)} .mkv file(s). Extracting audio...\n")

    success, skipped, failed = 0, 0, 0

    for filename in mkv_files:
        input_path = os.path.join(folder_path, filename)
        output_path = os.path.join(folder_path, os.path.splitext(filename)[0] + ".wav")

        if os.path.exists(output_path):
            print(f"[SKIP]    {filename} → .wav already exists.")
            skipped += 1
            continue

        try:
            (
                ffmpeg
                .input(input_path)
                .output(output_path, map="0:a:0", acodec="pcm_s16le", ar=16000, ac=1)
                .overwrite_output()
                .run(quiet=True)
            )
            print(f"[OK]      {filename} → {os.path.basename(output_path)}")
            success += 1

        except ffmpeg.Error as e:
            stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else "No details."
            if "matches no streams" in stderr:
                print(f"[NO AUDIO] {filename} — no audio stream found.")
            else:
                print(f"[FAIL]     {filename} — ffmpeg error:\n           {stderr.strip()}")
            failed += 1

    print(f"\nDone. ✓ {success} extracted  ⊘ {skipped} skipped  ✗ {failed} failed.")


for season in SEASONS:
    extract_audio(f"./Seinfeld/Season{season}")