#!/bin/bash
# Using hardcoded absolute paths (more reliable)

# Run Django server in a new terminal
gnome-terminal -- bash -c "source /home/koghalai/Desktop/websiteTesting/venv/bin/activate; cd /home/koghalai/Desktop/websiteTesting/my_tennis_club; python manage.py runserver; exec bash"

# Small delay to let Django start up
sleep 1

# Run ngrok in another new terminal
gnome-terminal -- ngrok http --url=master-piranha-remarkably.ngrok-free.app 8000
