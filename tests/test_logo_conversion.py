import os
import sys
from PIL import Image


def test_png_to_ico_conversion():
    """Convert PNG logo to ICO format."""
    # Define paths
    # When running from tests directory, assets is one level up
    png_path = os.path.join("tests","video2text_logo.png")
    ico_path = os.path.join("tests","video2text_logo.ico")

    # Debug: Print the paths
    print(f"Checking PNG path: {png_path}")
    print(f"Full resolved path: {os.path.abspath(png_path)}")
    print(f"PNG exists: {os.path.exists(png_path)}")

    # Check if PNG exists
    if not os.path.exists(png_path):
        print(f"PNG file not found at {png_path}")
        return False

    try:
        # Open PNG image
        with Image.open(png_path) as img:
            # Convert to RGBA if not already (ICO supports RGBA)
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # Save as ICO with multiple sizes
            icon_sizes = [
                (16, 16),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            ]
            img.save(ico_path, format="ICO", sizes=icon_sizes)

        # Verify ICO was created
        if os.path.exists(ico_path):
            print(f"Successfully converted {png_path} to {ico_path}")
            return True
        else:
            print(f"ICO file was not created at {ico_path}")
            return False

    except Exception as e:
        print(f"Error during conversion: {e}")
        return False


if __name__ == "__main__":
    success = test_png_to_ico_conversion()
    sys.exit(0 if success else 1)
