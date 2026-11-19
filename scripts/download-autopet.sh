#!/bin/sh
# Usage: scripts/download-autopet.sh [output folder=data_autopet] [PatientID...]
# Downloads autoPET (FDG-PET-CT-Lesions, TCIA, CC BY 4.0) patients as <output>/<PatientID>/<YYYY-MM-DD>/{CT,PT,SEG}/.
# Without PatientIDs: the three example patients.
set -eu
OUT=${1:-data_autopet}
[ $# -gt 0 ] && shift
PATIENTS=${*:-PETCT_15f4b7254f PETCT_1956667fce PETCT_642d6c78d6}
NBIA=https://services.cancerimagingarchive.net/nbia-api/services/v1

for patient in $PATIENTS; do
  echo "== $patient"
  curl -sSf "$NBIA/getSeries?Collection=FDG-PET-CT-Lesions&PatientID=$patient" | python3 -c '
import json, sys
for s in json.load(sys.stdin):
    print(s["SeriesInstanceUID"], s["StudyDate"][:10], s["Modality"], s.get("ImageCount", ""))' |
  while read -r uid date modality count; do
    dir="$OUT/$patient/$date/$modality"
    if [ -d "$dir" ] && [ "$(ls "$dir" | grep -c '\.dcm$')" -ge "${count:-1}" ]; then
      echo "  $date $modality: already there"
      continue
    fi
    echo "  $date $modality: downloading $count images"
    mkdir -p "$dir"
    curl -sSf "$NBIA/getImage?SeriesInstanceUID=$uid" -o "$dir/series.zip"
    unzip -qo "$dir/series.zip" -d "$dir"
    rm "$dir/series.zip"
  done
done
echo "Done: $OUT"
