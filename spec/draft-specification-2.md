# FactoryHorde Subnet Specification

## 1. Objective and scope

Build a subnet where miners compete to provide software factories: runnable systems that turn application specifications into working software. The validator evaluates generated applications against their specifications, and the results determine miner scores and rewards.

The reusable factory is the digital asset. Customers must be able to run it with their own model credentials. Miners control their agent harness, models, prompts, and development workflow, including planning, testing, and review, within the execution restrictions below. They may build on existing open-source software factories.

The first version evaluates generation of small applications. The long-term objective is sustained application development over months or years. Evaluation will expand to sequences of feature additions, bug fixes, and refactoring on the same application, testing whether the factory can make changes without progressively degrading the software.

SecretFucker, which uses trusted execution environment (TEE) technology, is the intended direction for future confidential execution. SecretFucker integration is outside the first version. Product communication should convey the long-term factory vision while distinguishing it from implemented capabilities.

## 2. Execution architecture

### Generation and evaluation

Miners submit runnable containers containing their factories. For each evaluation run, the execution system:

1. Retrieves the miner's submission and container.
2. Starts the factory in an isolated environment with access to the model proxy.
3. Supplies an application specification through an input volume.
4. Collects the generated repository from an output volume.
5. Evaluates the application against explicit requirements and records its score.

The validator uses the scores to calculate and set miner weights.

### Private submissions

A miner publishes an on-chain commitment containing a URL to a hosted JSON file, for example in S3 object storage. The file contains an entry for each authorized validator, keyed by its validator identity. Each entry carries information encrypted for that validator:

- The location of the factory image.
- The model-provider credentials needed to run it, supporting multiple providers where necessary.

Initially, miners authorize the owner's validator. The format must allow additional validators to be authorized later by adding encrypted entries.

This keeps submission details private from other miners and unauthorized validators. Authorized validators can decrypt the information and must therefore be trusted with it. Encryption for a validator does not protect credentials or factory contents from that validator.

### Model access and networking

Containers have no direct external network access. All permitted external requests pass through an operator-controlled proxy.

For inference, the factory sends requests without provider credentials; the proxy authenticates them using the credentials supplied for that run. Evaluation uses miner-provided credentials, while a customer can supply its own credentials when running the same factory.

The proxy supports an approved set of model providers and aggregators. It must not permit arbitrary miner-operated endpoints.

### Isolation

Miner containers and generated applications are untrusted workloads. Their execution must be isolated from validator infrastructure and secrets. Containerization alone is not a sufficient security boundary; the isolation design remains to be specified.

## 3. Validation and deployment

Use a single owner-operated validator for the first version. Generation and evaluation can produce different results on repeated runs; obtaining consistent independent scores across multiple validators would add cost and complexity.

Other parties are not assumed to be prevented from registering validators or setting weights, but access to private submissions and miner credentials requires miner authorization. The submission format provides a path to adding trusted validators later; coordination between them remains future design work.

Deploy the factory on subnet 12, mechanism 1, alongside the existing Compute Horde workload. Preserve the existing service and its current user obligations.

The immediate delivery target is a complete working subnet using Nexus's local testing facilities. Early deployment on mechanism 1 with zero emissions, using the team's existing validator hotkey, is the next target.

## 4. Tasks and evaluation

Start with small, affordable application tasks. Around two or three tasks per day is the proposed initial frequency, to be adjusted based on observed costs and participation.

The proposed task-generation process is to use software categories and publicly observable product functionality as inspiration, then produce self-contained specifications describing required features and user flows. Computer-use agents may assist with exploration. Lightweight human selection should exclude illegal or otherwise unsuitable applications.

Task diversity must discourage memorizing a fixed catalogue or pre-packaging solutions. Specific protections against overfitting remain open.

Evaluate adherence to explicit requirements. Model-assisted inspection, generated tests, test execution, and comparison of submissions on a common task remain candidate methods.

A proposed evaluator check is to package existing open-source software factories as miners and compare their results. Also do comparisons across factory versions.

## 5. Baseline miner, costs, and rewards

Provide an accessible baseline miner that participants can configure and extend. The proposed starting implementation remains Pi with OpenRouter free models. Verify available limits and compatibility with the proxy during implementation. Miners may choose other supported providers and workflows.

Miners supply credentials and pay for generation inference. They choose the tradeoff between output quality and model cost; reducing cost while maintaining quality increases their net return. The validator operator pays validation costs. The team will operate the initial validator and can run a baseline miner for testing and initial participation.

Rewards should encourage quality without making duplicate or nearly identical miners profitable simply because they occupy more UIDs. Minimum quality thresholds and concentrating rewards on the best miner, and dust emissions for qualifying submissions are a potential starting point.

## 6. Nexus implementation

Use Nexus to implement the subnet.

Fix defects and missing shared capabilities within Nexus.

Verify Nexus's support for container execution, the single-validator arrangement, and mechanism 1.

## 7. Open decisions and implementation details

| Area | Still to resolve |
| --- | --- |
| Execution contract | Submission schema, encryption/key handling, container image retrieval and access controls, input/output formats, runtime requirements, and artifact storage. |
| Execution controls | Workload isolation, resource limits, timeouts, spending caps, and credential handling. |
| Proxy | Implementation or existing project to adopt, supported providers, request contract, and policy for search and other non-model access. |
| Network access | Non-model internet access must use the proxy, allow-list. |
| Tasks | Generation and human-selection process, diversity and secrecy, protections against overfitting, and initial task size and frequency. |
| Evaluation | Correctness and feature coverage, partial credit, usability and visual quality, reproducibility, score explanations, disputes, and handling run-to-run variation. Later, regression and quality across successive changes. |
| Economics and launch | Team inference accounts and initial funding; reward formula, quality threshold, duplicate/tie handling and burn policy; emission allocation and public launch timing. |
| Commercial delivery | Customer-operated factories, hosted generation, or both, and eventual licensing terms. |
| Future validation | Conditions for adding validators, delegation arrangements where needed, and how independent evaluations would be coordinated. |

## 8. Immediate work and first milestone

The first milestone is a complete working run, from an encrypted miner submission through container execution, repository collection, application evaluation, and weight setting in Nexus's local test environment. It must exercise isolation and proxy-mediated model access.

Record observed costs, evaluation problems, and missing integration capabilities. An early zero-emission deployment on mechanism 1 may accompany or follow this work under the deployment arrangement above.
