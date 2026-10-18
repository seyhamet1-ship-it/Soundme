import asyncio
import subprocess
from pathlib import Path

import edge_tts

async def main():
    txt = 'Hello, welcome to Soundme. This is a short English test sentence for dubbing.'
    mp3 = Path('sample_voice.mp3')
    t = edge_tts.Communicate(txt, voice='en-US-JennyNeural')
    await t.save(str(mp3))
    out = Path('sample_input.mp4')
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=1280x720:d=6',
        '-i', str(mp3), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(out)
    ], check=True)
    print(f'created {out}: exists={out.exists()} size={out.stat().st_size if out.exists() else 0}')

asyncio.run(main())
