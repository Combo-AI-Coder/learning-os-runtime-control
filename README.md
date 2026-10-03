# Learning OS — Runtime-Control

The minimal public deployment authority for Learning OS V0.4.

`deployment.yaml` owns only deployment ID/topology/epoch/write state and the
numeric repository ID plus exact commit of Core. Instance identity, private
project-design lineage, migration authorization, receipts, learner state and
credentials are forbidden here.

Repository names are navigation metadata. Numeric repository IDs are security
identity. Ordinary Runtime may read this repository but has no write
capability; deployment mutation is a narrow maintenance CAS.

Publication validation and its non-deployment boundary: [Transition CI](docs/TRANSITION-CI.md).
