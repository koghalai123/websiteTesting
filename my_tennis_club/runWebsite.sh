#!/bin/bash
# Get the absolute path to the websiteTesting directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && cd .. && pwd)"

# Run Django server in a new terminal (closes when command ends)
gnome-terminal -- bash -c "source '$SCRIPT_DIR/venv/bin/activate'; cd '$SCRIPT_DIR/my_tennis_club'; python manage.py runserver; exec bash"

# Small delay to let Django start up
sleep 1

# Run ngrok in another new terminal (closes when command ends)
gnome-terminal -- ngrok http --url=master-piranha-remarkably.ngrok-free.app 8000
