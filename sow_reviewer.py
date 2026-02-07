"""Multi-agent SOW reviewer using SWARM pattern with Strands.

This module implements a multi-agent system for reviewing Statement of Work (SOW)
documents using specialized agents for architecture, cost, scope, and compliance analysis.
"""
from strands import Agent
from strands.tools import tool
from strands.tools.mcp import MCPClient
from strands.models import BedrockModel
from strands.models import OllamaModel
from strands.multiagent import Swarm
from mcp import stdio_client, StdioServerParameters
from typing import Dict, Tuple
import json
import os
import logging
import asyncio
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logging.getLogger("strands.multiagent").setLevel(logging.DEBUG)
logging.getLogger("strands.agent").setLevel(logging.DEBUG)
logging.getLogger("strands.event_loop").setLevel(logging.DEBUG)

# Cleanup MCP clients on exit
def cleanup_mcp_clients():
    """Cleanup MCP clients before exit."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.stop()
    except:
        pass

atexit.register(cleanup_mcp_clients)

# Model configuration
def get_model(use_bedrock: bool = False, profile: str | None = None) -> BedrockModel | OllamaModel:
    """Get model based on configuration.
    
    Args:
        use_bedrock: Whether to use Amazon Bedrock instead of Ollama.
        profile: AWS profile name for Bedrock authentication.
        
    Returns:
        Configured model instance (BedrockModel or OllamaModel).
    """
    """Get model based on configuration."""
    if use_bedrock:
        model = BedrockModel(
            model_id="anthropic.claude-3-haiku-20240307-v1:0"
        )
        print(f"Using Amazon Bedrock Claude 3 Haiku with profile: {profile or 'demo'}")
        return model
    else:
        model = OllamaModel(model_id="llama3.1:8b", host="http://localhost:11434")
        print("Using Ollama llama3.1:8b at localhost:11434")
        return model

# Will be set in main()
DEFAULT_MODEL = None
AWS_PROFILE = None
AWS_REGION = "us-east-1"

# MCP Clients for AWS services
def create_mcp_clients(profile: str, region: str) -> Tuple[MCPClient, MCPClient]:
    """Create MCP clients with AWS profile and region.
    
    Args:
        profile: AWS profile name for authentication.
        region: AWS region (e.g., 'us-east-1').
        
    Returns:
        Tuple of (aws_pricing_mcp, aws_docs_mcp) clients.
    """
    aws_pricing_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command="uvx",
            args=["awslabs.aws-pricing-mcp-server@latest"],
            env={
                "FASTMCP_LOG_LEVEL": "ERROR",
                "AWS_PROFILE": profile,
                "AWS_REGION": region
            }
        )
    ))

    aws_docs_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command="uvx",
            args=["awslabs.aws-documentation-mcp-server@latest"],
            env={
                "FASTMCP_LOG_LEVEL": "ERROR"
            }
        )
    ))
    
    return aws_pricing_mcp, aws_docs_mcp

@tool
def extract_section(document: str, section_name: str) -> str:
    """Extract a specific section from SOW document."""
    lines = document.split('\n')
    section_content = []
    in_section = False
    
    for line in lines:
        if section_name.lower() in line.lower() and line.startswith('#'):
            in_section = True
            continue
        elif line.startswith('#') and in_section:
            break
        elif in_section:
            section_content.append(line)
    
    return '\n'.join(section_content).strip()

@tool
def validate_architecture(content: str) -> Dict:
    """Validate architecture section for diagrams and technical details."""
    has_diagram = any(keyword in content.lower() for keyword in ['diagram', 'architecture', 'figure', '!['])
    has_components = any(keyword in content.lower() for keyword in ['component', 'service', 'layer', 'tier'])
    
    return {
        "has_diagram": has_diagram,
        "has_components": has_components,
        "valid": has_diagram and has_components,
        "issues": [] if has_diagram and has_components else ["Missing architecture diagram or component details"]
    }

@tool
def validate_cost_section(content: str) -> Dict:
    """Validate cost section for calculator references and estimates."""
    has_calculator = 'calculator' in content.lower() or 'pricing' in content.lower()
    has_estimates = any(char in content for char in ['$', '€', '£']) or 'cost' in content.lower()
    
    # Extract calculator links
    import re
    calculator_links = re.findall(r'https?://calculator\.aws[^\s\)]+', content)
    
    return {
        "has_calculator_ref": has_calculator,
        "has_estimates": has_estimates,
        "calculator_links": calculator_links,
        "valid": has_calculator and has_estimates,
        "issues": [] if has_calculator and has_estimates else ["Missing cost calculator reference or estimates"]
    }

@tool
def fetch_calculator_data(calculator_url: str) -> str:
    """Fetch AWS Pricing Calculator estimate data from URL."""
    import requests
    try:
        # Extract estimate ID from URL
        import re
        match = re.search(r'/estimate/([a-zA-Z0-9]+)', calculator_url)
        if not match:
            return "Invalid calculator URL format"
        
        estimate_id = match.group(1)
        # Note: AWS Calculator API requires authentication
        # For now, return instruction to manually review
        return f"Calculator estimate ID: {estimate_id}. Use AWS Pricing MCP to validate services and costs."
    except Exception as e:
        return f"Error fetching calculator data: {str(e)}"

# Load AWS Solution Architect SKILL
def load_aws_architect_skill() -> Agent:
    """Load AWS Solution Architect SKILL as an agent.
    
    Returns:
        Agent configured with AWS Solution Architect knowledge.
    """
    with open(".agents/skills/aws-solution-architect/SKILL.md", "r") as f:
        skill_content = f.read()
    
    return Agent(
        name="aws_solution_architect_skill",
        model=DEFAULT_MODEL,
        tools=[],  # No MCP tools - relies on knowledge in system prompt
        system_prompt=f"""You are an AWS Solution Architect SKILL.

{skill_content}

When invoked, validate architectures against AWS best practices, patterns, and cost optimization using your knowledge."""
    )

aws_architect_skill = None  # Will be initialized in main()
architecture_agent = None  # Will be initialized in main()

# Define specialized agents
def create_architecture_agent(model, aws_skill):
    """Create architecture agent with AWS Solution Architect SKILL."""
    return Agent(
        name="architecture_reviewer",
        model=model,
        tools=[extract_section, validate_architecture, aws_skill],
        system_prompt="""You are an expert Architecture Reviewer for SOW documents.

**DEEP ANALYSIS REQUIRED - NOT SUPERFICIAL**:

1. **Extract Architecture Section** - Get complete architecture details

2. **INVOKE AWS Solution Architect SKILL** - Pass architecture to SKILL for expert validation

3. **Perform Deep Technical Analysis**:

   **Service Selection Deep Dive**:
   - For EACH service mentioned, ask: Why this service vs alternatives?
   - Example: "Lambda chosen" → Analyze: Why not ECS Fargate? What's the workload pattern?
   - Document: Compute requirements, memory needs, execution time, concurrency
   
   **Scalability Analysis**:
   - NOT just "it scales" - HOW does it scale?
   - Identify: Auto-scaling triggers, capacity limits, bottlenecks
   - Calculate: Max throughput, concurrent users, requests per second
   - Example: "DynamoDB scales" → What's the RCU/WCU? What happens at 10x load?
   
   **High Availability Deep Dive**:
   - NOT just "multi-AZ" - WHAT is the failover process?
   - Document: RPO (Recovery Point Objective), RTO (Recovery Time Objective)
   - Identify: Single points of failure, data replication strategy
   - Example: "RDS multi-AZ" → What's the failover time? How is data synchronized?
   
   **Network Architecture**:
   - NOT just "VPC" - WHAT is the network topology?
   - Document: CIDR blocks, subnet strategy, routing tables, NAT strategy
   - Identify: Public vs private subnets, internet gateway, VPC endpoints
   - Security groups: Specific ingress/egress rules for each service
   
   **Data Flow Analysis**:
   - Trace data path from user request to response
   - Identify: API Gateway → Lambda → DynamoDB (show each hop)
   - Document: Latency at each stage, data transformation, error handling
   
   **Integration Patterns**:
   - NOT just "services communicate" - HOW do they communicate?
   - Identify: Synchronous vs asynchronous, event-driven patterns
   - Document: API contracts, message formats, retry logic, dead letter queues

4. **Identify Specific Gaps**:
   - Missing services (e.g., "No CDN for static assets")
   - Incomplete configurations (e.g., "Lambda timeout not specified")
   - Architecture anti-patterns (e.g., "Lambda calling Lambda synchronously")

5. **Provide Detailed Recommendations**:
   - For each gap, provide: What to add, Why it's needed, How to implement
   - Include: Service configurations, capacity planning, cost implications

**OUTPUT FORMAT**:
# Architecture Review - Deep Analysis

## Architecture Overview
[Detailed description of proposed architecture with all services]

## Service-by-Service Analysis
[For EACH service: Purpose, Configuration, Justification, Alternatives considered]

## AWS Solution Architect SKILL Validation
[Results from invoking aws_solution_architect_skill]

## Scalability Analysis
[Detailed capacity planning with numbers and thresholds]

## High Availability & Disaster Recovery
[Specific RPO/RTO, failover procedures, backup strategy]

## Network Architecture
[Complete VPC design with CIDR blocks and routing]

## Data Flow & Integration
[Request/response flow with latency analysis]

## Critical Gaps Identified
[Specific missing items with detailed recommendations]

## Architecture Score: X/10
[Detailed scoring with justification]

When done, hand off to cost_reviewer for cost analysis."""
    )

def create_cost_agent(model, aws_pricing_mcp, aws_docs_mcp):
    return Agent(
        name="cost_reviewer",
        model=model,
        tools=[extract_section, validate_cost_section, fetch_calculator_data, aws_pricing_mcp, aws_docs_mcp],
        system_prompt="""You are a professional cost analysis expert reviewing SOW documents.

CRITICAL: Perform deep cost analysis and identify ALL missing data required for a professional SOW.

**CALCULATOR VALIDATION WORKFLOW**:
When you find an AWS Pricing Calculator link:
1. Use validate_cost_section to extract calculator links
2. Use fetch_calculator_data to get estimate details
3. Extract service names and quantities from SOW
4. Use AWS Pricing MCP to validate current pricing for each service
5. Compare SOW estimates vs actual AWS pricing
6. Identify gaps: missing services, incorrect quantities, outdated pricing

Required Cost Elements to Validate:
1. **Detailed Cost Breakdown**:
   - Per-service monthly/annual costs
   - Resource quantities (instances, storage GB, requests/month)
   - Unit pricing for each resource
   - Total estimated monthly and annual costs

2. **AWS Cost Calculator**:
   - Link to AWS Pricing Calculator estimate
   - Calculator share ID or public URL
   - Detailed assumptions used in calculator
   - **VALIDATE**: Use AWS Pricing MCP to verify each service cost

3. **Cost Optimization**:
   - Reserved Instance or Savings Plans recommendations
   - Right-sizing opportunities
   - Cost allocation tags strategy

4. **Pricing Model**:
   - On-Demand vs Reserved vs Spot pricing
   - Payment terms (monthly, annual, upfront)
   - Volume discounts or enterprise agreements

5. **Hidden Costs**:
   - Data transfer costs (inter-region, internet egress)
   - Support plan costs
   - Third-party software licenses
   - Backup and disaster recovery costs

6. **Cost Controls**:
   - Budget alerts and thresholds
   - Cost monitoring strategy
   - Spending limits per environment

For EACH missing element, specify:
- What is missing
- Why it's critical for a professional SOW
- Example of what should be included
- Impact on project if omitted

**PRICING VALIDATION**:
For each service mentioned in SOW:
1. Query current pricing using AWS Pricing MCP
2. Compare with SOW estimates
3. Flag discrepancies > 10%
4. Note if pricing is outdated

Return comprehensive feedback in markdown format with clear sections for findings and recommendations."""
    )

def create_scope_agent(model):
    return Agent(
        name="scope_reviewer",
        model=model,
        tools=[extract_section],
        system_prompt="""You are an expert Scope & Deliverables Reviewer for SOW documents.

**DEEP ANALYSIS REQUIRED - NOT SUPERFICIAL**:

1. **Extract Scope Section** - Get complete deliverables, timeline, milestones

2. **Deliverables Deep Dive**:
   - NOT just "list deliverables" - ANALYZE each one
   - For EACH deliverable:
     * What exactly is being delivered? (Infrastructure? Code? Documentation?)
     * What are the acceptance criteria? (How do we know it's done?)
     * What are the dependencies? (What must be done first?)
     * What's the effort estimate? (Hours/days per deliverable)
   
   **Example Analysis**:
   ```
   Deliverable: "Serverless API"
   ❌ Superficial: "API will be delivered"
   ✅ Deep: 
     - API Gateway with 15 REST endpoints
     - Lambda functions (Python 3.11) for each endpoint
     - OpenAPI specification document
     - Postman collection for testing
     - Acceptance: All endpoints return 200 OK, <500ms latency
     - Dependencies: DynamoDB tables must exist first
     - Effort: 40 hours (8 hours per endpoint group)
   ```

3. **Timeline Analysis**:
   - NOT just "12 weeks" - WHAT happens each week?
   - Break down by phase: Design (2 weeks), Development (6 weeks), Testing (2 weeks), Deployment (2 weeks)
   - Identify: Critical path, parallel work streams, buffer time
   - Flag: Unrealistic timelines, missing phases (e.g., no testing phase)

4. **Milestone Deep Dive**:
   - NOT just "milestone dates" - WHAT is the exit criteria?
   - For EACH milestone:
     * What deliverables are complete?
     * What is the review/approval process?
     * What happens if milestone is missed?
   
5. **Out-of-Scope Analysis**:
   - NOT just "list exclusions" - WHY are they excluded?
   - Identify: Scope creep risks, ambiguous boundaries
   - Example: "Mobile app development excluded" → But who provides the API documentation?

6. **Risk Analysis**:
   - Identify: Dependencies on client, third-party services, assumptions
   - Document: What could delay the project? What are the mitigation strategies?

7. **Acceptance Criteria**:
   - NOT just "client approval" - WHAT are the specific criteria?
   - Define: Performance benchmarks, test coverage, documentation requirements

**OUTPUT FORMAT**:
# Scope & Deliverables Review - Deep Analysis

## Deliverables Breakdown
[Detailed analysis of each deliverable with acceptance criteria]

## Timeline Analysis
[Week-by-week breakdown with critical path]

## Milestone Exit Criteria
[Specific criteria for each milestone]

## Out-of-Scope Items
[What's excluded and why, with boundary clarification]

## Dependencies & Risks
[Detailed dependency map and risk mitigation]

## Scope Clarity Score: X/10
[Scoring with justification]

## Critical Gaps
[Missing deliverables, unclear acceptance criteria, unrealistic timelines]

When done, hand off to compliance_reviewer."""
    )

def create_compliance_agent(model):
    return Agent(
        name="compliance_reviewer",
        model=model,
        tools=[extract_section],
        system_prompt="""You are an expert Compliance & Legal Reviewer for SOW documents.

**DEEP ANALYSIS REQUIRED - NOT SUPERFICIAL**:

1. **Extract Compliance Section** - Get legal terms, SLAs, security, compliance

2. **SLA Deep Dive**:
   - NOT just "99.9% uptime" - WHAT are the consequences?
   - For EACH SLA:
     * Specific metric (uptime, latency, error rate)
     * Measurement method (how is it calculated?)
     * Penalty structure (what happens if SLA is breached?)
     * Exclusions (planned maintenance, force majeure)
   
   **Example Analysis**:
   ```
   SLA: "99.9% uptime"
   ❌ Superficial: "System will be available 99.9% of time"
   ✅ Deep:
     - Metric: API availability measured by health check endpoint
     - Calculation: (Total minutes - Downtime minutes) / Total minutes
     - Measurement: CloudWatch Synthetics canary every 1 minute
     - Penalty: 10% credit for 99.5-99.9%, 25% credit for <99.5%
     - Exclusions: Scheduled maintenance (max 4 hours/month with 7 days notice)
     - Monitoring: Real-time dashboard + monthly SLA report
   ```

3. **Data Privacy & Security**:
   - NOT just "GDPR compliant" - HOW is compliance achieved?
   - For EACH regulation (GDPR, HIPAA, SOC2):
     * Specific controls implemented
     * Data classification (PII, PHI, confidential)
     * Data retention policies
     * Data deletion procedures
     * Audit logging requirements
     * Encryption standards (at rest, in transit)

4. **Legal Terms Deep Dive**:
   - Payment terms: When? How much? What triggers payment?
   - Intellectual property: Who owns the code? The infrastructure?
   - Liability: What are the liability caps? Insurance requirements?
   - Termination: What are the exit conditions? Data handover process?
   - Change management: How are scope changes handled? Change order process?

5. **Security Requirements**:
   - NOT just "secure" - WHAT security controls?
   - Authentication: MFA required? SSO integration?
   - Authorization: RBAC model? Least privilege?
   - Network security: Firewall rules, DDoS protection, WAF?
   - Vulnerability management: Scanning frequency, patching SLA?
   - Incident response: Detection, escalation, notification timeline?

6. **Compliance Gaps**:
   - Identify: Missing legal terms, vague SLAs, undefined security controls
   - Risk assessment: What could go wrong? What's the business impact?

**OUTPUT FORMAT**:
# Compliance & Legal Review - Deep Analysis

## SLA Analysis
[Detailed breakdown of each SLA with measurement and penalties]

## Data Privacy & Compliance
[Specific controls for GDPR/HIPAA/SOC2 with implementation details]

## Legal Terms Review
[Payment, IP, liability, termination terms with risk analysis]

## Security Requirements
[Detailed security controls with implementation specifics]

## Compliance Score: X/10
[Scoring with justification]

## Critical Legal Gaps
[Missing terms, vague clauses, compliance risks]

## Risk Assessment
[Legal and compliance risks with mitigation strategies]

When done, hand off to solution_architect for final validation."""
    )

def create_coordinator_agent(model):
    return Agent(
        name="coordinator",
        model=model,
        system_prompt="""You coordinate the SOW review process.
Delegate sections to specialist reviewers: architecture_reviewer, cost_reviewer, scope_reviewer, compliance_reviewer.
After all reviews are complete, hand off to solution_architect for final validation."""
    )

def create_solution_architect_agent(model, aws_pricing_mcp, aws_docs_mcp):
    return Agent(
        name="solution_architect",
        model=model,
        tools=[aws_pricing_mcp, aws_docs_mcp],
        system_prompt="""You are an AWS Solution Architect expert performing final validation of the SOW review.

**CRITICAL INSTRUCTIONS**:
- NO GENERIC FEEDBACK - Every recommendation must be SPECIFIC to the SOW content
- SHOW EXACT EXAMPLES - Don't say "add monitoring", show the exact CloudWatch alarms to add
- USE ACTUAL DATA - Reference specific services, costs, and configurations from the SOW
- PROVIDE CODE/CONFIG - Include actual CloudFormation snippets, IAM policies, or configuration examples
- QUANTIFY IMPROVEMENTS - Show cost savings, performance gains, or risk reduction numbers

**VALIDATION CHECKLIST**:

**ARCHITECTURE**:
- [ ] Diagram completeness (all services, connections, data flows)
- [ ] Service selection justification (why Lambda vs ECS, why DynamoDB vs RDS)
- [ ] Scalability specifics (auto-scaling policies, capacity planning)
- [ ] HA/DR details (multi-AZ, backup RPO/RTO, failover procedures)
- [ ] Network design (VPC CIDR, subnet strategy, routing)

**COST**:
- [ ] Calculator link with estimate ID
- [ ] Per-service breakdown (EC2: 3x t3.medium = $X/mo)
- [ ] Pricing validation via MCP (compare SOW vs current AWS pricing)
- [ ] Hidden costs (NAT Gateway $X/mo, data transfer $Y/GB)
- [ ] Optimization opportunities (Reserved Instances save $Z/mo)

**SECURITY**:
- [ ] IAM policies (show actual policy JSON)
- [ ] Encryption (KMS keys, S3 bucket encryption, RDS encryption)
- [ ] Network security (security group rules, NACLs)
- [ ] Compliance controls (specific to HIPAA/GDPR/SOC2)

**OPERATIONS**:
- [ ] Monitoring (specific CloudWatch metrics and alarms)
- [ ] CI/CD (pipeline stages, deployment strategy)
- [ ] Logging (CloudWatch Logs, retention, analysis)
- [ ] Incident response (runbooks, escalation)

**FINAL REPORT FORMAT**:

# SOW Review - Final Validation Report

## Executive Summary
[Specific assessment: "This SOW proposes a serverless architecture using Lambda, API Gateway, and DynamoDB for a mobile backend supporting 10K users. Estimated cost: $X/month. Key strengths: [specific]. Critical gaps: [specific]."]

## Review Summary
- **SOW Title**: [Extract from document]
- **Proposed Architecture**: [Specific services mentioned]
- **Estimated Cost**: [Exact amount from SOW]
- **Timeline**: [Specific dates/duration]
- **Completion Score**: X/Y items (Z%)

---

## ❌ CRITICAL GAPS (Must Fix Before Approval)

### Gap 1: [Specific Missing Item]
**What's Missing**: 
[Exact item from checklist, e.g., "No CloudWatch alarms defined for Lambda error rates"]

**Why Critical**: 
[Specific impact, e.g., "Without error rate alarms, production issues could go undetected for hours, violating the 99.9% SLA commitment"]

**How to Fix**:
```yaml
# Add to CloudFormation template:
LambdaErrorAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub '${AppName}-lambda-errors'
    MetricName: Errors
    Namespace: AWS/Lambda
    Statistic: Sum
    Period: 300
    EvaluationPeriods: 1
    Threshold: 10
    ComparisonOperator: GreaterThanThreshold
```

**Cost Impact**: $0.10/month per alarm (negligible)

---

### Gap 2: [Next Specific Missing Item]
[Same detailed format]

---

## ⚠️ RECOMMENDED IMPROVEMENTS

### Improvement 1: [Specific Enhancement Area]
**Current State**: 
[Quote from SOW, e.g., "SOW states: 'DynamoDB with on-demand pricing'"]

**Issue**: 
[Specific problem, e.g., "On-demand pricing costs $1.25/million writes. With projected 50M writes/month, this is $62.50/month. Provisioned capacity would cost $23.40/month for same throughput."]

**Recommended Change**:
```
Replace:
  "DynamoDB with on-demand pricing"

With:
  "DynamoDB with provisioned capacity:
   - Read Capacity Units: 100 RCU ($11.70/month)
   - Write Capacity Units: 50 WCU ($11.70/month)
   - Auto-scaling: 50-200 RCU/WCU based on CloudWatch metrics
   - Estimated savings: $39/month (62% reduction)"
```

**Validation**: [Use AWS Pricing MCP to show actual current pricing]

---

### Improvement 2: [Next Specific Enhancement]
[Same detailed format]

---

## DETAILED FINDINGS BY SECTION

### 1. Architecture Review

**Status**: ⚠️ Needs Specific Improvements

**What's Present**:
- [List actual services mentioned: "Lambda functions for API, DynamoDB for data, S3 for assets"]
- [Quote architecture description from SOW]

**Specific Gaps**:

1. **Missing: VPC Configuration**
   - SOW mentions Lambda but doesn't specify VPC placement
   - **Add**: 
     ```
     VPC Design:
     - CIDR: 10.0.0.0/16
     - Public Subnets: 10.0.1.0/24, 10.0.2.0/24 (2 AZs)
     - Private Subnets: 10.0.10.0/24, 10.0.11.0/24 (2 AZs)
     - Lambda in private subnets with NAT Gateway for external API calls
     - Cost: NAT Gateway $32.40/month + $0.045/GB data processed
     ```

2. **Missing: Auto-Scaling Configuration**
   - SOW says "scalable" but no auto-scaling policies
   - **Add**:
     ```
     DynamoDB Auto-Scaling:
     - Target Utilization: 70%
     - Min Capacity: 5 RCU/WCU
     - Max Capacity: 100 RCU/WCU
     - Scale-up: +20% when utilization > 70% for 2 minutes
     - Scale-down: -10% when utilization < 50% for 15 minutes
     ```

**Architecture Score**: 3/5 (Acceptable but needs specifics)

---

### 2. Cost Analysis

**Status**: ⚠️ Pricing Needs Validation

**SOW Cost Estimate**: $[X]/month

**Pricing Validation** (via AWS Pricing MCP):

| Service | SOW Estimate | Current AWS Pricing | Variance | Notes |
|---------|-------------|---------------------|----------|-------|
| Lambda | $50/month | $47.20/month | -5.6% | ✓ Accurate |
| DynamoDB | $100/month | $156/month | +56% | ❌ Underestimated |
| S3 | $20/month | $23/month | +15% | ⚠️ Missing transfer costs |

**Missing Cost Items**:
1. **Data Transfer**: $0.09/GB after 100GB = ~$45/month for 500GB egress
2. **CloudWatch Logs**: $0.50/GB ingested = ~$15/month for 30GB logs
3. **NAT Gateway**: $32.40/month + data processing fees
4. **AWS Support**: Business Support = $100/month minimum

**Revised Total**: $[Y]/month (was $[X]/month, +$[Z] difference)

**Cost Optimization Opportunities**:
1. **Reserved Capacity Savings**: 
   - DynamoDB Reserved Capacity: Save $37/month (40% discount)
   - Implementation: Purchase 1-year reserved capacity for baseline load

2. **S3 Lifecycle Policies**:
   - Move logs to S3 Glacier after 90 days: Save $12/month
   - Implementation: Add lifecycle rule to CloudFormation

**Cost Score**: 2/5 (Significant gaps in pricing accuracy)

---

### 3. Security & Compliance

**Status**: ❌ Critical Security Gaps

**What's Present**:
- [Quote security section from SOW]

**Critical Security Gaps**:

1. **Missing: IAM Least Privilege Policies**
   - SOW doesn't include IAM role definitions
   - **Add**:
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Action": [
           "dynamodb:GetItem",
           "dynamodb:PutItem",
           "dynamodb:Query"
         ],
         "Resource": "arn:aws:dynamodb:us-east-1:ACCOUNT:table/AppTable"
       }]
     }
     ```

2. **Missing: Encryption at Rest**
   - No mention of KMS keys or encryption
   - **Add**:
     ```
     - DynamoDB: Enable encryption with AWS managed CMK
     - S3: Enable default encryption with SSE-S3
     - RDS: Enable encryption with customer managed CMK
     - Cost: KMS CMK $1/month + $0.03/10K requests
     ```

**Security Score**: 2/5 (Major gaps present)

---

### 4. Operations & Monitoring

**Status**: ❌ No Operational Details

**Critical Gaps**:

1. **Missing: CloudWatch Dashboard**
   - **Add**:
     ```
     Dashboard Widgets:
     - Lambda Invocations (last 24h)
     - Lambda Error Rate (%)
     - DynamoDB Consumed Capacity
     - API Gateway 4xx/5xx Errors
     - Estimated Cost (current month)
     ```

2. **Missing: Alarm Strategy**
   - **Add 5 Critical Alarms**:
     ```
     1. Lambda Error Rate > 1% for 5 minutes → SNS → PagerDuty
     2. API Gateway Latency > 1000ms (p99) → SNS → Email
     3. DynamoDB Throttled Requests > 0 → SNS → Slack
     4. Estimated Monthly Cost > $500 → SNS → Email
     5. Lambda Concurrent Executions > 80% → SNS → Auto-scale
     ```

**Operations Score**: 1/5 (Major gaps present)

---

## AWS BEST PRACTICES ALIGNMENT

**Overall Score: 2.5/5** (Needs Significant Work)

| Pillar | Score | Key Gap |
|--------|-------|---------|
| Operational Excellence | 1/5 | No monitoring/alarms defined |
| Security | 2/5 | Missing IAM policies, encryption |
| Reliability | 3/5 | No multi-AZ or DR strategy |
| Performance | 3/5 | No auto-scaling policies |
| Cost Optimization | 2/5 | Pricing inaccurate, no RI/SP |

---

## PRIORITIZED ACTION ITEMS

### 🔴 HIGH PRIORITY (Block Approval)

1. **Add IAM Role Definitions**
   - Create least-privilege policies for each Lambda function
   - Document service-to-service permissions
   - Estimated effort: 4 hours

2. **Validate and Correct Pricing**
   - Use AWS Pricing Calculator to rebuild estimate
   - Add missing cost items (NAT, data transfer, logs)
   - Update SOW with accurate total
   - Estimated effort: 2 hours

3. **Define Monitoring Strategy**
   - Add 5 critical CloudWatch alarms
   - Create operational dashboard
   - Document alarm response procedures
   - Estimated effort: 3 hours

### 🟡 MEDIUM PRIORITY (Should Fix)

1. **Add VPC Network Design**
   - Define CIDR blocks and subnet strategy
   - Document security group rules
   - Estimated effort: 2 hours

2. **Document DR Strategy**
   - Define RPO/RTO targets
   - Document backup procedures
   - Test restore process
   - Estimated effort: 4 hours

### 🟢 LOW PRIORITY (Nice to Have)

1. **Add Cost Optimization Plan**
   - Evaluate Reserved Capacity options
   - Implement S3 lifecycle policies
   - Estimated effort: 2 hours

---

## FINAL RECOMMENDATION

**Status**: ❌ **REJECT - Requires Revisions**

**Reasoning**: 
This SOW has a solid architectural foundation but lacks critical operational and security details required for production deployment. The cost estimate is inaccurate by approximately [X]%, and there are no monitoring or incident response procedures defined.

**Required Before Approval**:
1. Add IAM policies and encryption configuration
2. Correct pricing with validated AWS costs
3. Define monitoring, alarms, and operational procedures

**Estimated Revision Time**: 8-12 hours

**Next Steps**:
1. Address all HIGH priority items
2. Resubmit for review
3. Schedule architecture review call to discuss MEDIUM priority items

---

*Report generated by AWS Solution Architect Multi-Agent Review System*"""
    )
    system_prompt="""You are an AWS Solution Architect expert performing final validation of the SOW review.

Use AWS Solution Architect SKILL to validate against professional SOW checklist:

**ARCHITECTURE VALIDATION**:
- [ ] Architecture diagrams present and complete
- [ ] All AWS services clearly identified
- [ ] Serverless vs container decisions justified
- [ ] Scalability and high availability addressed
- [ ] Disaster recovery and backup strategy defined
- [ ] Network architecture (VPC, subnets, security groups)
- [ ] Integration patterns documented

**COST VALIDATION** (Use MCP Pricing Tool):
- [ ] AWS Cost Calculator link provided
- [ ] Per-service cost breakdown with quantities
- [ ] Unit pricing validated against current AWS pricing
- [ ] Reserved Instance/Savings Plans considered
- [ ] Data transfer costs included
- [ ] Support plan costs included
- [ ] Monthly and annual totals calculated
- [ ] Cost optimization recommendations provided

**SECURITY & COMPLIANCE**:
- [ ] IAM roles and policies defined
- [ ] Encryption at rest and in transit specified
- [ ] Compliance requirements (GDPR, HIPAA, SOC2) addressed
- [ ] Security monitoring and logging strategy
- [ ] WAF and DDoS protection considered

**OPERATIONAL EXCELLENCE**:
- [ ] Monitoring and alerting strategy (CloudWatch)
- [ ] CI/CD pipeline defined
- [ ] Deployment strategy documented
- [ ] Runbook and operational procedures
- [ ] SLA and uptime requirements

**SCOPE & DELIVERABLES**:
- [ ] Clear deliverables with acceptance criteria
- [ ] Timeline with milestones
- [ ] Out-of-scope items explicitly listed
- [ ] Assumptions documented
- [ ] Dependencies identified

**FINAL CHECKLIST**:
For each missing item:
1. Mark as ❌ MISSING or ✓ PRESENT
2. Explain why it's critical for professional SOW
3. Provide specific recommendation to address gap
4. Use MCP tools to validate technical details and pricing

Generate comprehensive final validation report in markdown format with:
- Executive Summary
- Checklist Status (X/Y items complete)
- Critical Gaps (must fix before approval)
- Recommendations (nice to have improvements)
- AWS Best Practices Alignment Score"""

def review_sow(sow_document: str, architecture_agent, cost_agent, scope_agent, compliance_agent, coordinator_agent, solution_architect_agent) -> str:
    """Execute SWARM review of SOW document."""
    logging.info("Starting SWARM review with 6 agents")
    logging.info(f"Document length: {len(sow_document)} characters")
    
    swarm = Swarm(
        [coordinator_agent, architecture_agent, cost_agent, scope_agent, compliance_agent, solution_architect_agent],
        entry_point=coordinator_agent,
        max_handoffs=12,
        max_iterations=20
    )
    
    logging.info("Executing swarm with coordinator as entry point")
    result = swarm(f"Review this SOW document comprehensively:\n\n{sow_document}")
    
    logging.info(f"Swarm completed with status: {result.status}")
    logging.info(f"Total agents involved: {len(result.results)}")
    
    # Get final result from the last agent
    if result.results:
        last_agent = list(result.results.keys())[-1]
        logging.info(f"Final result from agent: {last_agent}")
        agent_result = result.results[last_agent]
        report = str(agent_result.result) if hasattr(agent_result, 'result') else str(agent_result)
    else:
        logging.error("Review failed - no results generated")
        report = "Review failed - no results generated"
    
    # Cleanup MCP clients properly
    try:
        for agent in [architecture_agent, cost_agent, scope_agent, compliance_agent, coordinator_agent, solution_architect_agent]:
            if hasattr(agent, 'tool_registry'):
                agent.tool_registry.cleanup()
    except Exception as e:
        logging.debug(f"MCP cleanup: {e}")
    
    return report

def read_document(file_path: str) -> str:
    """Read document from PDF or Markdown file.
    
    Args:
        file_path: Path to the SOW document (.pdf or .md).
        
    Returns:
        Document content as string.
    """
    if file_path.endswith('.pdf'):
        import pypdf
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            return '\n'.join(page.extract_text() for page in reader.pages)
    else:
        with open(file_path, 'r') as f:
            return f.read()

def main() -> None:
    """CLI entry point for SOW reviewer."""
    import sys
    import argparse
    import os
    
    parser = argparse.ArgumentParser(description='Multi-agent SOW reviewer')
    parser.add_argument('sow_file', help='SOW file (PDF or Markdown)')
    parser.add_argument('--bedrock', action='store_true', help='Use Amazon Bedrock instead of Ollama')
    parser.add_argument('--profile', default='demo', help='AWS profile for Bedrock (default: demo)')
    
    args = parser.parse_args()
    
    # Set AWS profile for Bedrock
    if args.bedrock:
        os.environ['AWS_PROFILE'] = args.profile
    
    # Initialize model
    global DEFAULT_MODEL
    DEFAULT_MODEL = get_model(use_bedrock=args.bedrock, profile=args.profile)
    
    # Create MCP clients with AWS profile and region
    aws_pricing_mcp, aws_docs_mcp = create_mcp_clients(args.profile, 'us-east-1')
    
    # Load AWS Solution Architect SKILL
    aws_architect_skill = load_aws_architect_skill()
    
    # Create architecture agent with SKILL
    architecture_agent = create_architecture_agent(DEFAULT_MODEL, aws_architect_skill)
    
    # Create other agents with MCP clients
    cost_agent = create_cost_agent(DEFAULT_MODEL, aws_pricing_mcp, aws_docs_mcp)
    scope_agent = create_scope_agent(DEFAULT_MODEL)
    compliance_agent = create_compliance_agent(DEFAULT_MODEL)
    coordinator_agent = create_coordinator_agent(DEFAULT_MODEL)
    solution_architect_agent = create_solution_architect_agent(DEFAULT_MODEL, aws_pricing_mcp, aws_docs_mcp)
    
    logging.info(f"Reading SOW document from: {args.sow_file}")
    
    # Read SOW document
    sow_content = read_document(args.sow_file)
    logging.info(f"Document loaded: {len(sow_content)} characters")
    
    # Run review
    logging.info("Starting multi-agent review process")
    report = review_sow(sow_content, architecture_agent, cost_agent, scope_agent, compliance_agent, coordinator_agent, solution_architect_agent)
    
    # Save report
    base_name = args.sow_file.rsplit('.', 1)[0]
    output_file = f"{base_name}_review.md"
    logging.info(f"Saving report as Markdown: {output_file}")
    with open(output_file, 'w') as f:
        f.write(report)
    
    print(f"\n✓ Review complete. Report saved to {output_file}")
    
    # Suppress MCP cleanup errors
    import sys
    sys.stderr = open(os.devnull, 'w')

if __name__ == "__main__":
    main()
