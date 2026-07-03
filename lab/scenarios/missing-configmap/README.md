# Scenario: missing-configmap (SCAFFOLD — not wired for MVP)

Planned fault: Deployment mounts/`envFrom` a ConfigMap that does not exist → pods stuck in
`CreateContainerConfigError` / CrashLoopBackOff.

- **Expected fix:** create the missing ConfigMap (or remove the reference).
- **Validation:** rollout status Complete + pods Ready.

Implement in a later chunk (see specs/004-minikube-lab-design.md catalog).
