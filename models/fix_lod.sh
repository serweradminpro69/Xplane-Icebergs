#!/bin/bash
MODELS_DIR="/home/miki/.steam/steam/steamapps/common/X-Plane 12/Custom Scenery/Xplane-Icebergs/models"
LOD_FAR=50000   # meters -- how far away they'll stay visible

for f in "$MODELS_DIR"/*.obj; do
    if grep -q "^ATTR_LOD" "$f"; then
        sed -i -E "s/^ATTR_LOD[[:space:]]+[0-9.]+[[:space:]]+[0-9.]+/ATTR_LOD\t0 $LOD_FAR/" "$f"
        echo "Patched existing ATTR_LOD in: $f"
    else
        sed -i "0,/^TRIS/s//ATTR_LOD\t0 $LOD_FAR\nTRIS/" "$f"
        echo "Inserted ATTR_LOD before first TRIS in: $f"
    fi
done
echo "Done."
