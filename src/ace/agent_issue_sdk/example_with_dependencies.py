#!/usr/bin/env python3
"""Example: Creating issues with automatic dependency documentation."""

import asyncio
from ace.agent_issue_sdk.client import IssueCreator, IssueContent


async def example_with_dependencies():
    """Example showing how to use the new dependencies and blocks fields."""

    creator = IssueCreator()

    # Example 1: Issue with no dependencies (foundation issue)
    foundation_issue = IssueContent(
        title="Setup: Initialize infrastructure",
        target_repository="example-repo",
        description="Set up the base infrastructure needed for the project.",
        acceptance_criteria=[
            "Create necessary resources",
            "Document setup process",
        ],
        dependencies=[],  # Empty list means no dependencies
        blocks=["#2", "#3"],  # This issue blocks issues #2 and #3
    )

    # Example 2: Issue with dependencies (depends on foundation)
    dependent_issue = IssueContent(
        title="Feature: Build on foundation",
        target_repository="example-repo",
        description="Build the next feature using the foundation from issue #1.",
        acceptance_criteria=[
            "Use foundation from #1",
            "Implement feature",
        ],
        dependencies=["#1"],  # Must complete #1 first
        blocks=["#4"],  # This issue blocks issue #4
    )

    # Example 3: Issue with multiple dependencies
    complex_issue = IssueContent(
        title="Feature: Advanced feature",
        target_repository="example-repo",
        description="Advanced feature that depends on multiple prior issues.",
        acceptance_criteria=[
            "Requires #1 and #2 to be complete",
            "Implement advanced logic",
        ],
        dependencies=["#1", "#2"],  # Must complete both #1 and #2
        blocks=[],  # This is a leaf node, doesn't block anything
    )

    print("✅ Dependencies are now automatically documented in issue bodies!")
    print("\nWhen you create an issue with dependencies:")
    print("- The 'Dependencies' section is automatically added")
    print("- The 'Blocks' section shows what this issue unblocks")
    print("- Agents can immediately see the dependency chain")
    print("\nExample issue body structure:")
    print("""
## Description
[Your description here]

## Dependencies
**MUST COMPLETE FIRST:**
- #1
- #2

## Blocks
- #4

## Acceptance Criteria
[Your criteria here]
    """)


if __name__ == "__main__":
    asyncio.run(example_with_dependencies())
