#!/bin/sh
while :
do
	git pull
	./feeder.py http://auto-sheriff.appspot.com/data
	sleep 1 # Allow time for second control-C
done
