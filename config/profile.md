# Job Search Profile

## Machine Config

```json
{
  "profile_version": 1,

  "target_role": "Senior Java Backend Engineer",
  "experience": "8+ years",

  "target_titles": [
    "Senior Java Backend Engineer",
    "Senior Java Engineer",
    "Senior Backend Engineer",
    "Senior Software Engineer",
    "Backend Software Engineer",
    "Java Software Engineer",
    "JVM Engineer",
    "Senior Platform Engineer",
    "Distributed Systems Engineer",
    "Application Engineer",
    "Lead Software Engineer",
    "Staff Software Engineer"
  ],

  "strong_title_terms": [
    "java",
    "jvm",
    "backend",
    "back-end",
    "distributed systems",
    "spring",
    "microservices"
  ],

  "software_title_terms": [
    "software engineer",
    "software developer",
    "backend engineer",
    "platform engineer",
    "application engineer",
    "java developer"
  ],

  "seniority_terms": [
    "senior",
    "sr",
    "staff",
    "lead",
    "principal",
    "engineer iii",
    "engineer iv",
    "developer iii",
    "developer iv"
  ],

  "excluded_title_terms": [
    "qa engineer",
    "quality assurance",
    "sdet",
    "test engineer",
    "devops engineer",
    "site reliability engineer",
    "sre",
    "frontend engineer",
    "front-end engineer",
    "data scientist",
    "machine learning scientist",
    "civil engineer",
    "mechanical engineer",
    "electrical engineer",
    "manufacturing engineer",
    "supplier engineer",
    "facilities engineer",
    "property engineer",
    "intern",
    "internship",
    "new grad",
    "engineering manager",
    "software engineering manager",
    "director"
  ],

  "location_priority": [
    "US Remote",
    "Dallas-Fort Worth",
    "Austin",
    "San Antonio",
    "Exceptional other US"
  ],

  "preferred_location_terms": [
    "remote",
    "dallas",
    "fort worth",
    "dfw",
    "plano",
    "frisco",
    "irving",
    "richardson",
    "addison",
    "allen",
    "mckinney",
    "coppell",
    "lewisville",
    "grapevine",
    "arlington",
    "las colinas",
    "carrollton",
    "austin",
    "san antonio"
  ],

  "allow_other_us_locations_for_strong_match": true,

  "preferred_employment_types": [
    "direct full-time",
    "W2 contract",
    "contract-to-hire"
  ],

  "prefer_direct_employer": true,
  "allow_reputable_staffing_firms": true,

  "visa_status": "H-1B",
  "h1b_start_date": "2026-10-01",
  "requires_h1b_transfer_or_future_sponsorship": true,

  "pay_floor_base_usd": null,

  "candidate_history_retention_days": 45,
  "late_discovery_hours": 48,

  "secondary_web_search_hours": 72
}
```

## Search Requirements

Backend is the strongest preference.

Prioritize Java, Spring Boot, microservices, REST APIs, Kafka, distributed systems, event-driven architecture, databases, AWS/Azure, cloud-native systems, APIs, platform engineering, and large-scale production systems.

Prefer Senior individual-contributor roles. Strong hands-on Lead roles and realistic Staff backend roles are acceptable.

Do not require Java to appear in the job title when the actual backend stack is strongly transferable.

For remote positions, identify restrictions when present, including:

- permitted or excluded states
- required hub city
- distance from office
- time-zone requirements
- hybrid/office attendance
- relocation requirements

Identify employment arrangement when present:

- direct employer
- W2 contract
- contract-to-hire
- staffing/recruiting firm

### Sponsorship rules

Exclude a role when the actual job description explicitly contains requirements such as:

- no sponsorship
- no visa sponsorship
- cannot sponsor now or in the future
- must not require sponsorship now or in the future
- unrestricted work authorization required
- U.S. persons only
- U.S. citizen required
- Green Card required
- security clearance requirements incompatible with the candidate
- ITAR/U.S.-person restrictions incompatible with the candidate

If the wording says **"No new H-1B"**, do not automatically exclude it. Flag:

`H-1B transfer may be possible - verify`

Do not infer sponsorship solely from historical H-1B filings.

## Resume

### SAI AVINASH GORLA
Senior Java Backend Engineer  
Dallas, TX

### Professional Summary

Senior Java backend engineer with 8+ years of experience building Spring Boot microservices and event-driven data pipelines on AWS and Azure. At General Motors, led backend design for automating vehicle-program onboarding in VEIS and architected Kafka-based services in Matador that ingest and audit millions of manufacturing records per day from GM plants worldwide. Earlier experience includes dealer-facing systems used by 1,000+ dealers at Daimler Trucks North America and statewide claims processing at Blue Cross Blue Shield. Known for owning designs end to end and replacing manual, error-prone workflows with reliable, automated backend services.

### Technical Skills

**Languages & Backend:** Java (8-21), SQL, Spring Boot, Spring MVC, Spring Security, Spring Data JPA, Spring Batch, Hibernate

**APIs & Distributed Systems:** RESTful APIs (JAX-RS), SOAP, Apache Kafka, Apache Camel, Microservices, Event-Driven Architecture, Distributed Systems

**Cloud & DevOps:** AWS (EC2, S3, Lambda), Azure, Docker, Kubernetes, Jenkins, Azure DevOps, Argo Workflows, CI/CD, Pivotal Cloud Foundry (PCF)

**Data & Tools:** PostgreSQL, Oracle, DB2, MySQL, MongoDB, DynamoDB, Redis, SonarQube, Splunk, Datadog, JUnit, Mockito, Git, Maven, Postman, IntelliJ, Eclipse

**AI-Assisted Development:** GitHub Copilot, Claude Code, ChatGPT, AI-assisted code generation, debugging, test generation, documentation, and code review

**Frontend Exposure:** React, Angular

### Professional Experience

#### General Motors | Dallas, TX
**Senior Software Engineer | Oct 2021 - Present**

- Led backend design and delivery for automating new vehicle-program onboarding in VEIS with a team of 5 engineers, replacing a manual process where engineers hand-built onboarding files from three separate systems and cross-checked figures by hand with services that pull, validate, and assemble the data automatically.
- Chained 7 previously manual import and upload steps into a single end-to-end flow that runs unattended through publish and halts only on error, eliminating hand-offs between teams and reducing onboarding errors.
- Designed the logic that constructs wiring-circuit representations from raw engineering data, working through ambiguous source data to produce schematics consumed by dealers and service technicians through eDiagnostics.
- Architected event-driven Spring Boot and Kafka microservices in Matador that ingest, audit, and store millions of as-built manufacturing records per day from all GM ICE plants, powering downstream analytics and maintaining an S3 record for traceability and audit history.
- Diagnosed recurring audit failures caused by a timing mismatch between two upstream data streams, where one arrived up to an hour behind the other; redesigned the flow and built a re-audit microservice that automatically reprocesses failed records, cutting manual intervention by 60% and improving data accuracy before persistence in S3 and PostgreSQL.
- Replaced a manual, desktop-based 2D image generation process that required coordination across three teams and multi-day wait times with automated Argo Workflows pipelines that produce 5-6 views per part in about an hour, removing the cross-team hand-offs entirely.
- Built and owned 5 Matador microservices, including ingestion, audit, and supporting helper services, along with their Azure DevOps CI/CD pipelines for automated build, test, and deployment to Pivotal Cloud Foundry; partnered with DevOps to containerize workloads on Docker and Kubernetes.
- Used AI-assisted development tools to support Java backend development, debugging, test generation, documentation, and code review, while validating generated output against application requirements and engineering standards.
- Extended VEIS backend APIs for eDiagnostics integration and collaborated with frontend engineers on Angular workflows for schematics visualization, 3D model rendering, and program setup.

**Environment:** Java 17/21, Spring Boot, Kafka, Argo Workflows, Azure DevOps, PCF, AWS S3, PostgreSQL, Docker, Kubernetes, Splunk, Angular, Teamcenter

#### Daimler Trucks North America | Portland, OR
**Java Developer | Jul 2019 - Jul 2021**

- Built secure REST APIs and Spring MVC services using JAX-RS and Spring Security, connecting a Dealer Portal used by 1,000+ dealers across North America with internal parts, inventory, and ordering systems.
- Developed Spring Batch jobs for high-volume nightly parts and inventory processing, improving batch runtime and reliability by roughly 20% over the previous implementation and reducing manual re-runs.
- Configured Jenkins CI/CD pipelines for microservices and AWS Lambda functions, integrated SonarQube for automated code-quality checks, and containerized services on AWS EC2 with Docker and S3-backed storage and backups, standardizing releases across environments.
- Worked with DB2 and Hibernate/JPA for backend persistence, SQL development, and data access across dealer, parts, and inventory workflows.

**Environment:** Java 8, Spring Boot, Spring MVC, Spring Security, Spring Batch, Hibernate/JPA, DB2, AWS EC2, AWS S3, AWS Lambda, Jenkins, SonarQube, Docker

#### Blue Cross Blue Shield | Chicago, IL
**Java Developer | Apr 2018 - Jun 2019**

- Built Spring Boot microservices and Spring Batch jobs automating daily processing, scheduling, and statewide reporting for 20-50K nursing-facility medical claims per month for the State of New Mexico.
- Identified manual dependencies in the daily reporting and vendor-distribution workflow and automated scheduling, FTP delivery, and email notifications to 5+ downstream vendors, saving 5-10 hours per week of manual effort and improving delivery reliability.
- Implemented claim validation and business logic in Java, Hibernate, and SQL, improving consistency and reliability across claim-processing workflows.

**Environment:** Java 7/8, Spring Boot, Spring Batch, Hibernate, REST/SOAP, Oracle, Jenkins, JUnit, Mockito, IBM WebSphere

### Education

**M.S. in Computer Science**  
University of Central Missouri | Warrensburg, MO  
Dec 2017
