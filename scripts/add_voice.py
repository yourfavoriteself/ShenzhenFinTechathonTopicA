"""Put the recorded voiceover on the demo video, scene by scene.

    python -m scripts.add_voice                 # after make video; reads docs/media/voice_cues.json

The narration is read in one take, so its timing drifts from the picture. voice_cues.json says where
each line of docs/video_script.md starts and ends in the recording. Each line is placed at the start
of its scene. A line that does not fit before the next scene is first sped up a little (at most
MAX_TEMPO); if it still does not fit, the picture holds its last frame before the next scene for the
missing time. The same holds are applied to the subtitled copy, the subtitles and the caption timeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

MEDIA = Path("docs/media")
MAX_TEMPO = 1.06     # faster than this sounds hurried
GAP = 0.25           # silence between two lines, seconds
BEFORE = 0.6         # a hold starts this long before the next scene, on a still picture


def plan(cues: list[dict], starts: list[float], duration: float,
         still: dict[int, float] | None = None) -> tuple[list[dict], list[tuple[float, float]]]:
    """Where each line goes in the new video and which frames hold for how long.

    A scene holds just before the next one, or at still[i] when given (a title card holds while it is
    fully shown, not while it fades out)."""
    still = still or {}
    placed, holds, shift, prev_end = [], [], 0.0, 0.0
    for i, c in enumerate(cues):
        need = c["to"] - c["from"]
        last = i + 1 == len(cues)
        nxt = (duration if last else starts[i + 1]) + shift
        at = 0.0 if i == 0 else max(starts[i] + shift, prev_end + GAP)
        tempo = min(MAX_TEMPO, max(1.0, need / max(0.1, nxt - at - GAP)))
        end = at + need / tempo
        if end + GAP > nxt + 0.05:
            h = round(end + GAP - nxt, 2)
            holds.append((duration if last else round(still.get(i, starts[i + 1] - BEFORE), 2), h))
            shift += h
        placed.append({**c, "at": round(at, 3), "tempo": round(tempo, 3)})
        prev_end = end
    return placed, holds


def shifted(t: float, holds: list[tuple[float, float]]) -> float:
    return round(t + sum(h for at, h in holds if at <= t), 2)


def _video_filter(holds: list[tuple[float, float]], duration: float) -> str:
    cuts = [0.0] + [at for at, _ in holds if at < duration] + [duration]
    tail = sum(h for at, h in holds if at >= duration)
    parts = []
    for k in range(len(cuts) - 1):
        hold = holds[k][1] if k < len(cuts) - 2 else tail
        pad = f",tpad=stop_mode=clone:stop_duration={hold}" if hold else ""
        parts.append(f"[0:v]trim=start={cuts[k]}:end={cuts[k + 1]},setpts=PTS-STARTPTS{pad}[v{k}]")
    n = len(cuts) - 1
    return ";".join(parts) + ";" + "".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[v]"


def _audio_filter(placed: list[dict], total: float) -> str:
    parts = []
    for k, p in enumerate(placed):
        length = (p["to"] - p["from"]) / p["tempo"]
        parts.append(f"[1:a]atrim=start={p['from']}:end={p['to']},asetpts=PTS-STARTPTS,atempo={p['tempo']},"
                     f"afade=t=in:d=0.02,afade=t=out:st={max(0.0, length - 0.06):.3f}:d=0.06,"
                     f"adelay={int(p['at'] * 1000)}:all=1[a{k}]")
    mix = "".join(f"[a{k}]" for k in range(len(placed)))
    return (";".join(parts) + f";{mix}amix=inputs={len(placed)}:normalize=0,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=end={total:.3f}[a]")


def _srt_shift(text: str, holds: list[tuple[float, float]]) -> str:
    def ts(s: str) -> float:
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))

    def fmt(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    out = []
    for line in text.splitlines():
        if " --> " in line:
            a, b = line.split(" --> ")
            line = f"{fmt(shifted(ts(a), holds))} --> {fmt(shifted(ts(b), holds))}"
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", type=Path, default=MEDIA)
    a = ap.parse_args()
    meta_path = a.media / "guardian_demo_captions.json"
    meta = json.loads(meta_path.read_text())
    if "voice" in meta:
        raise SystemExit("the video already has the voice; record it again with make video first")
    cues = json.loads((a.media / "voice_cues.json").read_text())["cues"]
    starts = sorted([c["t"] for c in meta["captions"]] + [s["t"] for s in meta["slates"]])
    if len(starts) != len(cues):
        raise SystemExit(f"{len(cues)} voice lines for {len(starts)} scenes: the script changed, record the voice again")
    duration = meta["duration_s"]
    slates = {round(s["t"], 2): s for s in meta["slates"]}
    still = {i: slates[round(t, 2)]["end"] - 1.0 for i, t in enumerate(starts)
             if round(t, 2) in slates and slates[round(t, 2)].get("end")}
    placed, holds = plan(cues, starts, duration, still)
    total = duration + sum(h for _, h in holds)
    for name in ("guardian_demo", "guardian_demo_subtitled"):
        src = a.media / f"{name}.mp4"
        tmp = src.with_suffix(".voice.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(a.media / "voice.m4a"),
                        "-filter_complex", _video_filter(holds, duration) + ";" + _audio_filter(placed, total),
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "slow", "-crf", "23",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                        "-shortest", str(tmp)], check=True)
        tmp.replace(src)
    srt = a.media / "guardian_demo.srt"
    srt.write_text(_srt_shift(srt.read_text(), holds))
    for m in meta["captions"] + meta["slates"]:
        m["t"] = shifted(m["t"], holds)
        if m.get("end") is not None:
            m["end"] = shifted(m["end"], holds)
    meta["duration_s"] = round(total, 1)
    meta["voice"] = {"holds": [{"at": at, "seconds": h} for at, h in holds],
                     "lines": [{"scene": p["scene"], "at": p["at"], "tempo": p["tempo"]} for p in placed]}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"voice on {len(placed)} scenes; {len(holds)} holds, {sum(h for _, h in holds):.1f} s; video {total:.1f} s")


if __name__ == "__main__":
    main()
