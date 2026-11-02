"""Keep extracted dependencies/baselines out of repository-wide pytest scans.

Audit diagnostics are run explicitly in a separate process to avoid mixing
original and extracted exchange modules in the same Python import cache.
"""
collect_ignore = ['extracted', 'code']
