"""원문 이미지는 세션 메모리에서만 재사용한다. 변환 프로세스는 취소 시 종료한다."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import sys


def source_images(content, pages):
    import pdfplumber
    if not content or not pages or len(pages) > 2:
        raise ValueError('원문 이미지 또는 페이지 범위 오류')
    images = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for n in pages:
            if type(n) is not int or not 1 <= n <= len(pdf.pages):
                raise ValueError('페이지 범위 오류')
            data = BytesIO()
            pdf.pages[n-1].to_image(resolution=110).original.save(data, format='PNG')
            images.append('data:image/png;base64,' + base64.b64encode(data.getvalue()).decode())
    return tuple(images)


async def render_images(content, pages):
    worker = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'academy.image_worker',
        cwd=str(Path(__file__).resolve().parents[1]),
        creationflags=0x08000000 if sys.platform=='win32' else 0,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        payload = json.dumps({'pdf':base64.b64encode(content).decode(), 'pages':pages}).encode()
        output, _ = await worker.communicate(payload)
        if worker.returncode:
            raise ValueError('이미지 변환 실패')
        result = json.loads(output)
        if not isinstance(result,list) or not 1 <= len(result) <= 2 or any(not isinstance(im,str) or not im.startswith('data:image/png;base64,') for im in result):
            raise ValueError('이미지 변환 응답 오류')
        return tuple(result)
    finally:
        if worker.returncode is None:
            try:
                worker.kill()
            except ProcessLookupError:
                pass
            await worker.wait()
