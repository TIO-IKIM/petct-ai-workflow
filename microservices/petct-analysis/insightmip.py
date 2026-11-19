"""Puts the prior MIP (left) and the current MIP (right) side by side, with the scan dates on top."""
import argparse
import glob
import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pydicom
from PIL import Image


def scan_date(png):
    """Scan date from the PET DICOMs next to the png."""
    pet_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(png))), "dicom", "pet")
    for path in sorted(glob.glob(os.path.join(pet_dir, "*"))):
        try:
            date = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=["StudyDate"]).get("StudyDate")
            if date:
                return datetime.strptime(str(date)[:8], "%Y%m%d")
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subsequent", required=True, help="MIP (result.png) of the subsequent scan")
    parser.add_argument("--prior", default="", help="MIP of the prior scan; ignored if the file does not exist")
    parser.add_argument("--output", required=True, help="InsightMIP PNG to write")
    parser.add_argument("--copy", action="append", default=[], help="also write InsightMIP here (repeatable)")
    args = parser.parse_args()

    if not os.path.isfile(args.subsequent):
        raise SystemExit(f"ERROR: MIP of the subsequent scan {args.subsequent} not found")
    panels = []  # (image, title)
    subsequent_date = scan_date(args.subsequent)
    if args.prior and os.path.isfile(args.prior):
        prior_date = scan_date(args.prior)
        panels.append((Image.open(args.prior).convert("RGB"),
                       "Prior scan" + (f": {prior_date:%d.%m.%Y}" if prior_date else "")))
        interval = ""
        if prior_date and subsequent_date:
            interval = f" (+{(subsequent_date - prior_date).days / 30.44:.1f} months)"
        panels.append((Image.open(args.subsequent).convert("RGB"),
                       "Subsequent scan" + (f": {subsequent_date:%d.%m.%Y}" if subsequent_date else "") + interval))
    else:
        panels.append((Image.open(args.subsequent).convert("RGB"), ""))

    # same size so both are on the same scale
    width = max(image.width for image, _ in panels)
    height = max(image.height for image, _ in panels)
    fig, axes = plt.subplots(1, len(panels), facecolor="white", figsize=(8 * len(panels), 10),
                             layout="constrained", gridspec_kw=dict(wspace=0.04), squeeze=False)
    for ax, (image, title) in zip(axes[0], panels):
        ax.imshow(np.asarray(image.resize((width, height), Image.BILINEAR)))
        ax.axis("off")
        ax.set_title(title, fontsize=16, fontweight="bold", pad=10)
    for path in [args.output] + args.copy:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fig.savefig(path, bbox_inches="tight", dpi=300)
    plt.close(fig)
    print(f"InsightMIP from {len(panels)} time point(s): {[t for _, t in panels]} -> {args.output}")


if __name__ == "__main__":
    main()
