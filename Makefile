.PHONY: preflight create wait-ssh inventory provision scenario logs analyze destroy clean-stale

preflight:
	./scripts/pve-preflight.sh

create:
	./scripts/pve-create-run.sh "$${RUN_ID}"

wait-ssh:
	./scripts/pve-wait-ssh.sh "$${RUN_ID}"

inventory:
	./scripts/pve-generate-inventory.sh "$${RUN_ID}"

provision:
	ansible-playbook -i ansible/inventory.generated.ini ansible/site.generated.yml

scenario:
	./scripts/pve-run-scenario.sh "$${SCENARIO:-kernel-smoke}"

logs:
	./scripts/pve-collect-logs.sh "$${RUN_ID}"

analyze:
	./scripts/analyze-artifacts-gemini.py

destroy:
	./scripts/pve-destroy-run.sh "$${RUN_ID}"

clean-stale:
	./scripts/cleanup-stale-runs.sh --older-than-hours 24
