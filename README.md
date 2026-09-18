# Grove Swift Image Tools

Fast, simple image cropping, resizing and format conversion.

Version 1.1 currently provides Windows and MacOS downloads.

## Downloads

Download the newest version from [GitHub Releases](https://github.com/grahamlgrove/swift-image-tools/releases/latest):

- **Windows installer (MSI):** installs under Program Files, adds shortcuts and supports guided in-app updates.
- **Windows portable (ZIP):** extract the complete folder and run `Grove Swift Image Tools.exe`; no installation is required.

The application quietly checks GitHub Releases after startup. You can also choose **Check for updates** at any time. The installed edition downloads and launches the newer MSI after confirmation. The portable edition downloads the newer ZIP for extraction.

## Features

- Drag in individual images or whole folders.
- Queue and process multiple images while preserving separate settings for every image.
- Preview the original dimensions and select any crop area.
- Move the selection or resize it from every side and corner.
- Enter exact selected widths and heights.
- Crop without resizing, retaining the original format and 100% quality by default.
- Resize proportionally with presets of 2048, 1920, 1600, 1280, 800, 512, 256, 128 or 64 pixels, or use a custom width.
- Copy the processed selection to the clipboard at its specified resolution.
- Save beside the source or choose another output folder.
- Convert common formats plus TIFF, WebP, AVIF, JPEG XL, JPEG 2000, PSD, OpenEXR, TGA and supported camera RAW inputs.
- Never stretch or squash output images.
- Bundle ImageMagick so end users do not need to install it separately.

## Output names

Processed files are named in this form:

`originalname_converted_widthxheight.ext`

Existing files are not overwritten.

## Building on Windows

Requirements for maintainers:

- Python 3.12
- ImageMagick 7 at `C:\Program Files\ImageMagick-7.1.1-Q16-HDRI`
- WiX 3.14.1 portable tools under `.tools\wix314`

Run:

```powershell
.\package.ps1
```

The script builds the application, portable ZIP, genuine x64 MSI, and SHA-256 checksum file under `Release`.

## Licence

Grove Swift Image Tools is available under the [MIT License](LICENSE). Bundled components retain their own licences; see [Third-party notices](THIRD_PARTY_NOTICES.md).

## Support

Grove Swift Image Converter is developed and maintained as a personal hobby project. If you find it useful, you’re welcome to [[buy me a coffee](https://ko-fi.com/groveapps)](https://ko-fi.com/groveapps) and help support this and my other free apps and educational wikis.

Support is entirely optional and does not purchase additional features or services. Contributions are not tax deductible.
