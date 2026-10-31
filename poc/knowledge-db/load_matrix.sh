#!/bin/bash
for s in honcho aim-m1 aim-m2 aim-m3; do
  for c in 8 32 64; do python3 run_load.py $s $c 30; done
done
for s in honcho aim-m1 aim-m2; do python3 run_load.py $s 32 30 hot; done
