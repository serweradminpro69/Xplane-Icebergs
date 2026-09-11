#!/bin/bash

MODELS_DIR="/home/miki/.steam/steam/steamapps/common/X-Plane 12/Custom Scenery/Xplane-Icebergs/models"
TEXTURE_LINE="TEXTURE\t../textures/Blue_Ice_001_SD/Blue_Ice_001_COLOR.png"

for f in "$MODELS_DIR"/*.obj; do
    if grep -q -i "^TEXTURE" "$f"; then
        # Already has a TEXTURE line somewhere — replace it in place instead of adding a duplicate
        sed -i "s|^TEXTURE.*|$TEXTURE_LINE|" "$f"
        echo "Updated existing TEXTURE line in: $f"
    else
        # No TEXTURE line yet — insert one as line 4
        sed -i "4i $TEXTURE_LINE" "$f"
        echo "Inserted TEXTURE line into: $f"
    fi
done

echo "Done."
