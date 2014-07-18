#!/bin/sh
while :
do
    cd /src/build
    git pull --rebase

    cd /src/cycletimes/nannybot
	git pull

	./feeder.py http://auto-sheriff.appspot.com/data
	sleep 1 # Allow time for second control-C
done
