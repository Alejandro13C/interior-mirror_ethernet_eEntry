#!/usr/bin/env python3
"""Convert render-views PPM files to PNG and build same-frame comparison sheets."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--panini-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for file in args.input.glob('*.ppm'):
        with Image.open(file) as image:
            image.save(args.output / (file.stem + '.png'))
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:
        title_font = ImageFont.truetype(font_path, 30)
        label_font = ImageFont.truetype(font_path, 23)
    except OSError:
        title_font = ImageFont.load_default(size=30)
        label_font = ImageFont.load_default(size=23)
    width, height, header, caption = 640, 384, 110, 50
    if args.panini_only:
        files = sorted(args.output.glob('panini-fov*.png'),
                       key=lambda p: int(p.stem.removeprefix('panini-fov')))
        if not files:
            parser.error('No Panini images found')
        views = [('original-fit.png', 'Normal — imagen completa')]
        views += [(p.name, 'Panini — ' + p.stem.removeprefix('panini-fov') + '°') for p in files]
        columns = 4
        rows = (len(views) + columns - 1) // columns
        sheet = Image.new('RGB', (width*columns, header+(height+caption)*rows), '#141920')
        draw = ImageDraw.Draw(sheet)
        draw.text((20, 14), 'Panini — distintos campos de visión — mismo fotograma',
                  font=title_font, fill='white')
        draw.text((20, 60), 'Lente supuesta: 180° · Centro supuesto · Compresión 1 · Pitch 0° · Sin calibrar',
                  font=label_font, fill='#eac574')
        for i, (name, label) in enumerate(views):
            x = (i % columns)*width
            y = header+(i // columns)*(height+caption)
            draw.text((x+14, y+12), label, font=label_font, fill='white')
            with Image.open(args.output/name) as image:
                sheet.paste(image.resize((width, height), Image.Resampling.LANCZOS), (x, y+caption))
        sheet.save(args.output/'comparison-panini-all.png')
        print(args.output/'comparison-panini-all.png')
        return
    overview = Image.new('RGB', (width * 3, header + (height + caption) * 3), '#141920')
    draw = ImageDraw.Draw(overview)
    draw.text((20, 14), 'Normal / Panini / Rectilínea — mismo fotograma', font=title_font, fill='white')
    draw.text((20, 60), 'Lente equidistante supuesta: 180° · Centro supuesto · Sin calibrar · Pitch 0°',
              font=label_font, fill='#eac574')
    for row, fov in enumerate([110, 120, 130]):
        sheet = Image.new('RGB', (width * 3, header + caption + height), '#141920')
        painter = ImageDraw.Draw(sheet)
        painter.text((20, 14), f'Comparación con campo horizontal de salida de {fov}°',
                     font=title_font, fill='white')
        painter.text((20, 60), 'Modelo de lente supuesto de 180°; la imagen normal conserva el encuadre completo.',
                     font=label_font, fill='#eac574')
        views = [('original-fit', 'Normal — imagen completa'),
                 (f'panini-fov{fov}', f'Panini — {fov}° — compresión 1'),
                 (f'rectilinear-fov{fov}', f'Rectilínea — {fov}°')]
        for column, (name, label) in enumerate(views):
            with Image.open(args.output / (name + '.png')) as image:
                tile = image.resize((width, height), Image.Resampling.LANCZOS)
            x, y = column * width, header + row * (height + caption)
            draw.text((x + 14, y + 12), label, font=label_font, fill='white')
            overview.paste(tile, (x, y + caption))
            painter.text((x + 14, header + 12), label, font=label_font, fill='white')
            sheet.paste(tile, (x, header + caption))
        sheet.save(args.output / f'comparison-fov{fov}.png')
    overview.save(args.output / 'comparison-all.png')
    print(args.output / 'comparison-all.png')


if __name__ == '__main__':
    main()
