# Production checkpoint

Production v2 deployment has not been recorded yet.

After the public deployment passes every automatic and manual verification,
run `sudo bash /srv/amigo/deploy/checkpoint.sh` with the exact timestamped
rollback snapshot. The generated replacement records the production URL, Git
SHA, image IDs, verification facts, and rollback command without secrets.
