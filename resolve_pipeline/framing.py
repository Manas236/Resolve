"""FFmpeg framing for a vertical reel."""

# Use display aspect ratio so anamorphic video keeps its proportions.
VERTICAL_FRAME = (
    "scale=w='max(1080,ceil(1920*dar/2)*2)':"
    "h='max(1920,ceil(1080/dar/2)*2)',setsar=1,crop=1080:1920"
)
