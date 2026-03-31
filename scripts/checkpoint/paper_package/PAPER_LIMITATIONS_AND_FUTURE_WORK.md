# Limitations and Future Work

## Scope and Validation Boundaries

The approved checkpoint confirms functional validity for the current architecture and campaign conditions. However, the following boundaries should be clearly stated in the paper.

## Limitations

1. Environment specificity
- Primary validated host platform was macOS.
- Capture stack behavior may vary across OS, drivers, and permission models.

2. Network-topology constraints
- Remote validation was completed using same-network mobile traffic (iPhone) rather than a fully routed remote host.
- This is valid for physical-interface attribution (`en0`) but does not fully represent WAN-routed OT deployments.

3. Workload composition bias
- Traffic mix was heavily read-dominant (`READ_REQUEST`/`READ_RESPONSE`), with smaller write/exception fractions.
- Additional balanced workloads could improve external validity.

4. Temporal sensitivity
- State transitions are sampled through polling; observed transition timing depends on poll interval granularity.

5. Tooling-level assumptions
- Evaluator logic assumes specific acceptance windows and semantic mappings.
- Different operational contexts may require parameterized thresholds.

## Future Work

1. Cross-platform campaign replication
- Execute full CT1..CT7 campaigns on Linux and Windows agents under equivalent conditions.

2. Strict remote routed validation
- Run CT2 and CT5 with a routable remote source (VPN/tunnel) and compare with same-LAN behavior.

3. Extended protocol mix
- Add richer write-heavy and exception-heavy scenarios.
- Add mixed function-code distributions representative of industrial traces.

4. Statistical reporting
- Add repeated campaign runs and confidence intervals for key timing/transition metrics.

5. Reproducibility automation
- Add a single command orchestration script (`run_checkpoint.sh`) that emits frozen manifests and checksums.

## Paper framing recommendation

Present the current work as a validated functional baseline with strong practical evidence, and explicitly position the listed items as the next-stage validation roadmap.
