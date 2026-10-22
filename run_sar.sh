cd "$(dirname "$0")"
for d in $(cat sar_dates.txt); do
  grep -q "^$d," sar_done.txt 2>/dev/null && continue
  python ccf_results.py ../SAR/RAW_DATA/RESULTS/2026 SAR $d >/dev/null 2>&1 || { echo "$d,CCF_FAIL" >> sar_done.txt; continue; }
  python evaluate_card.py SAR $d --drf ../SAR/RAW_DATA/RACINGFORM/2026/SAR${d:4:4}.DRF --results results/SAR_${d}_results.csv --log results/performance_log_SAR.csv >/dev/null 2>&1 && echo "$d,ok" >> sar_done.txt || echo "$d,EVAL_FAIL" >> sar_done.txt
done
