"""Simple script to test HTML-to-PNG rendering."""

from pathlib import Path

from hlss.services.html_renderer import render_html_file_to_png


def main() -> None:
    html_path = Path("/app/screen_mockup.html")
    output_path = Path("/app/output/mockup.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    png_bytes = render_html_file_to_png(str(html_path), width=800, height=480)
    output_path.write_bytes(png_bytes)

    print(f"Rendered PNG saved to {output_path}")


if __name__ == "__main__":
    main()
