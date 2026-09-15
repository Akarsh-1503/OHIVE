"""Build the shipped bag-of-visual-words vocabulary.

Offline tool. Samples ORB descriptors from the demo clips, clusters them with
MiniBatchKMeans in the 256-dimensional *bit* space, binarises the centroids back
into ORB-shaped uint8 words and stores them with their tf-idf weights.

    python bench/build_vocab.py            # writes slam/vocab.npz

scikit-learn is needed here and only here; the runtime library does not import it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slam.config import SlamConfig  # noqa: E402
from slam.features import FeatureExtractor  # noqa: E402


def collect(
    videos: list[Path], cfg: SlamConfig, stride: int, per_frame: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return (descriptors, document ids) sampled across the clips."""
    fe = FeatureExtractor(cfg)
    rng = np.random.default_rng(cfg.seed)
    descs: list[np.ndarray] = []
    docs: list[np.ndarray] = []
    doc = 0
    for path in videos:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            print(f"  skip (cannot open) {path.name}")
            continue
        idx = 0
        kept = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                w = frame.shape[1]
                if w > cfg.target_width:
                    s = cfg.target_width / w
                    frame = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                _, _, d = fe.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
                if d.shape[0] > 0:
                    take = d if d.shape[0] <= per_frame else d[
                        rng.choice(d.shape[0], per_frame, replace=False)
                    ]
                    descs.append(take)
                    docs.append(np.full(take.shape[0], doc, dtype=np.int32))
                    doc += 1
                    kept += 1
            idx += 1
        cap.release()
        print(f"  {path.name}: {kept} frames sampled")
    if not descs:
        raise SystemExit("no descriptors collected")
    return np.concatenate(descs), np.concatenate(docs)


def build(desc: np.ndarray, docs: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    from sklearn.cluster import MiniBatchKMeans

    bits = np.unpackbits(desc, axis=1).astype(np.float32)
    km = MiniBatchKMeans(
        n_clusters=k, random_state=seed, batch_size=4096, n_init=3, max_iter=120
    )
    labels = km.fit_predict(bits)
    # Binarise back to ORB shape: a centroid coordinate above 0.5 means that bit
    # is set in the majority of the cluster's descriptors.
    words = np.packbits((km.cluster_centers_ > 0.5).astype(np.uint8), axis=1)

    n_docs = int(docs.max()) + 1
    df = np.zeros(k, dtype=np.float64)
    for w in range(k):
        sel = labels == w
        if sel.any():
            df[w] = np.unique(docs[sel]).size
    idf = np.log(n_docs / np.maximum(df, 1.0)).astype(np.float32)
    idf[df == 0] = 0.0
    return words.astype(np.uint8), idf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="*", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "slam" / "vocab.npz")
    ap.add_argument("--k", type=int, default=1024)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--per-frame", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    videos = args.videos or sorted((ROOT / "samples").glob("*.mp4"))
    if not videos:
        raise SystemExit("no videos found; pass --videos")
    print(f"sampling descriptors from {len(videos)} clip(s)")
    cfg = SlamConfig(seed=args.seed)
    desc, docs = collect(list(videos), cfg, args.stride, args.per_frame)
    print(f"  {desc.shape[0]} descriptors over {int(docs.max()) + 1} documents")
    words, idf = build(desc, docs, args.k, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, words=words, idf=idf)
    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {args.out} ({args.k} words, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
