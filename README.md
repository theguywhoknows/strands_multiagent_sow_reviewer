# Multi-Agent SOW Reviewer

SWARM pattern implementation using Strands Agents SDK for reviewing Statement of Work documents.

## Architecture

**SWARM Pattern**: Multiple specialized agents work in parallel on different SOW sections, coordinated by a central agent.

### Agents

1. **Architecture Reviewer** - Validates architecture diagrams, components, technical stack
2. **Cost Reviewer** - Validates AWS Cost Calculator references, pricing estimates
3. **Scope Reviewer** - Validates deliverables, timeline, milestones, acceptance criteria
4. **Compliance Reviewer** - Validates legal terms, SLAs, security, compliance standards
5. **Coordinator** - Orchestrates the review process and delegates to specialists
6. **Solution Architect** - Final validation using AWS architecture best practices and patterns

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```python
from sow_reviewer import SOWReviewerSwarm

# Load your SOW document
with open('your_sow.md', 'r') as f:
    sow_content = f.read()

# Run SWARM review
swarm = SOWReviewerSwarm()
report = swarm.review_sow(sow_content)

# Save report
with open('review_report.md', 'w') as f:
    f.write(report)
```

Or use the CLI:

```bash
# With Ollama (default)
python sow_reviewer.py sample_sow.md

# With Amazon Bedrock (profile: demo)
python sow_reviewer.py sample_sow.md --bedrock

# With Amazon Bedrock (custom profile)
python sow_reviewer.py sample_sow.md --bedrock --profile myprofile

# Output as PDF
python sow_reviewer.py sample_sow.md --output-pdf

# Bedrock + PDF output
python sow_reviewer.py sample_sow.pdf --bedrock --profile demo --output-pdf
```

# Review any file, output PDF
python sow_reviewer.py sample_sow.md --output-pdf
python sow_reviewer.py sample_sow.pdf --output-pdf
```

## Configuration

Set your Anthropic API key (or use Ollama):

```bash
# Use Claude (default if API key is set)
export ANTHROPIC_API_KEY="your-key-here"

# Or use Ollama (automatically used if no API key)
# Requires Ollama running locally with llama3.1:8b model
ollama pull llama3.1:8b
ollama serve
```

## PDF Output (Optional)

To enable PDF output, install system dependencies:

```bash
# macOS
brew install pango glib gobject-introspection
pip install weasyprint

# Ubuntu/Debian
sudo apt-get install libpango-1.0-0 libpangocairo-1.0-0
pip install weasyprint
```

## Tools

Each agent has access to specialized tools:

- `extract_section` - Extract specific sections from SOW
- `validate_architecture` - Check for diagrams and components
- `validate_cost_section` - Check for calculator refs and estimates
- `compile_review` - Merge section reviews into final report

## Output

Generates `sow_review_report.md` with:
- Executive summary
- Section-by-section reviews
- Validation results
- Issues and recommendations

## AWS Cost Calculator Integration

The cost reviewer agent validates references to:
- AWS Pricing Calculator: https://calculator.aws
- AWS Pricing MCP Server: https://awslabs.github.io/mcp/servers/aws-pricing-mcp-server
- AWS Documentation MCP: https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server

## Extending

Add new specialist agents:

```python
custom_agent = Agent(
    name="custom_reviewer",
    model="anthropic.claude-3-sonnet",
    tools=[your_tools],
    system_prompt="Your review instructions"
)

swarm.agents["custom"] = custom_agent
```
