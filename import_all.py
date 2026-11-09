"""DataSource 폴더 전체를 한 번에 데이터베이스로 가져오기

사용법:
  import_all.bat                     DataSource 전체 (암호 걸린 PDF는 건너뜀)
  import_all.bat 900101              PDF 암호(예: 생년월일 6자리)를 넣어 암호 PDF까지 읽기
  import_all.bat 900101 D:\\자료      암호 + 다른 폴더
  python import_all.py --password 900101 --folder D:\\자료

암호는 저장하지 않으며 이번 실행에만 씁니다.
"""
import os
import sys

from hab.config import load_config
from hab.service import get_service


def parse_args(argv):
    password, folder = None, None
    args = list(argv)
    while args:
        a = args.pop(0)
        if a in ("--password", "--pw", "-p") and args:
            password = args.pop(0)
        elif a in ("--folder", "-f") and args:
            folder = args.pop(0)
        elif os.path.isdir(a):
            folder = a
        elif password is None:
            password = a
    return password, folder


def main():
    cfg = load_config()
    password, folder = parse_args(sys.argv[1:])
    folder = folder or cfg["datasource_dir"]
    svc = get_service()
    print(f"DB: {cfg['db_path']}\n자료 폴더: {folder}")
    print(f"PDF 암호: {'입력됨' if password else '없음 (암호 걸린 PDF는 건너뜀)'}\n")
    res = svc.importer.import_folder(folder, {"password": password} if password else {})
    ok = sum(1 for r in res if r["status"] == "ok")
    new = sum(r.get("new", 0) for r in res)
    print(f"\n완료: 파일 {len(res)}개 중 {ok}개 처리, 새 거래 {new}건")
    need = [r for r in res if r["status"].startswith("need") or r["status"] == "error"]
    if need:
        print("확인 필요:")
        for r in need:
            print(" -", r["file"], r["message"])
        if any(r["status"] == "need_password" for r in need) and not password:
            print("\n암호 걸린 PDF를 읽으려면:  import_all.bat <PDF암호>")


if __name__ == "__main__":
    main()
