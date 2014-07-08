#!/bin/sh
while :
do
	git pull
	./feeder.py
	sleep 1 # Allow time for second control-C
done
