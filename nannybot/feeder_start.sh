#!/bin/sh
while :
do
	git pull
	./feeder.py http://auto-sheriff.appspot.com/data http://localhost:8080/data
	sleep 1 # Allow time for second control-C
done
