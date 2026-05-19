#!/bin/bash

# 프로젝트 루트 디렉토리로 이동 (필요 시 수정)
# cd /home/ubuntu/Crawler

# 로그 디렉토리 생성
mkdir -p logs
mkdir -p data

# 이미 실행 중인 스케줄러가 있다면 종료 (중복 실행 방지)
PID=$(pgrep -f "main.py --mode schedule")
if [ -n "$PID" ]; then
    echo "기존에 실행 중인 스케줄러(PID: $PID)를 종료합니다..."
    kill $PID
    sleep 2
fi

echo "크롤러 스케줄러를 백그라운드에서 실행합니다..."
# 통합된 main.py를 스케줄러 모드(--mode schedule)로 실행
nohup python3 main.py --mode schedule > logs/nohup.log 2>&1 &

echo "------------------------------------------------"
echo "스케줄러가 성공적으로 시작되었습니다."
echo "실시간 로그 확인: tail -f logs/nohup.log"
echo "백그라운드 프로세스 확인: ps -ef | grep 'main.py --mode schedule'"
echo "------------------------------------------------"
