"""신뢰한 서버가 보낸 JSON만 처리. 임시파일·키·데이터 로그를 만들지 않는다."""
import base64
import json
import sys
from .images import source_images


def main():
    try:
        data = json.loads(sys.stdin.buffer.read())
        result = source_images(base64.b64decode(data['pdf'],validate=True),tuple(data['pages']))
        sys.stdout.buffer.write(json.dumps(result).encode())
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
