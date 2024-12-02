import sys
from pathlib import Path
# 현재 파일의 절대 경로를 기준으로 루트 디렉토리로 이동
current_dir = Path(__file__).resolve()  # 현재 파일의 절대 경로
project_root = current_dir.parent.parent  # 두 단계 위의 디렉토리(프로젝트 루트)
sys.path.append(str(project_root))