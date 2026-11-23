"""Classify sampled frames from the portion of each clip Resolve will render."""
import subprocess

from resolve_pipeline.downloader import CONTAINERS

MODEL = "openai/clip-vit-base-patch32"
REVISION = "3d74acf"
TRAINING = (
    "a photo of a person lifting weights in a gym",
    "a photo of a person running or sprinting as training",
    "a photo of a person boxing or hitting a punching bag",
    "a photo of people doing an athletic workout or sports training",
)
OTHER = (
    "a screenshot of a cartoon or animated character",
    "a photo of a talking head or podcast interview",
    "a screenshot of a text slide or title card",
    "a screenshot of a video game",
    "a photo of a person standing or posing without exercising",
    "a photo of scenery or unrelated footage",
)


class TrainingVision:
    def __init__(self, threshold):
        from transformers import pipeline

        self.threshold = threshold
        self.model = f"{MODEL}@{REVISION}"
        self.classify = pipeline("zero-shot-image-classification", model=MODEL, revision=REVISION,
                                 model_kwargs={"use_safetensors": False}, device=-1)

    def screen(self, path, duration, timestamp, folder):
        start = max(0, min(timestamp, duration - 25))
        end = min(duration, start + 25)
        frames = []
        for index, fraction in enumerate((0.05, 0.25, 0.45, 0.65, 0.85)):
            second = start + fraction * (end - start)
            frame = folder / f"screen-{index}.jpg"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                 "-format_whitelist", ",".join(sorted(CONTAINERS)), "-ss", str(second), "-i", str(path),
                 "-vf", "scale=448:448:force_original_aspect_ratio=decrease", "-frames:v", "1",
                 "-threads", "1", str(frame)], check=True, timeout=20)
            if frame.is_file():
                frames.append((second, frame))
        return score_frames(frames, self.classify, self.threshold, self.model)


def score_frames(frames, classify, threshold, model=MODEL):
    scored = []
    for second, frame in frames:
        labels = {row["label"]: float(row["score"]) for row in
                  classify(str(frame), candidate_labels=list(TRAINING + OTHER), hypothesis_template="{}")}
        positive = sum(labels[label] for label in TRAINING)
        scored.append({"second": round(second, 3), "training": round(positive, 5),
                       "best_label": max(labels, key=labels.get),
                       "scores": {label: round(score, 5) for label, score in labels.items()},
                       "passed": positive >= threshold and max(labels, key=labels.get) in TRAINING})
    return {"model": model, "threshold": threshold,
            "accepted": len(scored) == 5 and sum(frame["passed"] for frame in scored) >= 3,
            "frames": scored}
