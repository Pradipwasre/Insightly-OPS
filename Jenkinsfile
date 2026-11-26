// Insightly CI/CD
//   every branch : lint, unit tests, guardrail + prompt-injection tests, backtest
//   main         : + live evals against Claude, build & push images to ECR, deploy to EKS
//
// Jenkins credentials to create (Manage Jenkins > Credentials):
//   anthropic-api-key  (Secret text)   your Anthropic API key, for live evals
//   aws-insightly      (AWS Credentials, CloudBees AWS Credentials plugin)  or run the agent on EC2 with an IAM role
// Agent needs: python3.12+, docker + buildx, aws cli v2, kubectl, helm, terraform.
pipeline {
  agent any
  options {
    timestamps()
    timeout(time: 60, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '30'))
    disableConcurrentBuilds()
  }
  parameters {
    booleanParam(name: 'RUN_INGEST', defaultValue: false, description: 'Reload RDS from S3 during deploy')
    booleanParam(name: 'SKIP_LIVE_EVALS', defaultValue: false, description: 'Skip the paid Claude eval subset')
  }
  environment {
    AWS_REGION = 'ap-south-1'
    CI = 'true'
    PYTHONUNBUFFERED = '1'
  }
  stages {
    stage('Checks') {
      steps {
        sh 'jenkins/scripts/run_checks.sh'
      }
      post {
        always { junit allowEmptyResults: true, testResults: 'reports/pytest.xml' }
      }
    }

    stage('Live evals') {
      when { allOf { branch 'main'; expression { !params.SKIP_LIVE_EVALS } } }
      steps {
        withCredentials([string(credentialsId: 'anthropic-api-key', variable: 'ANTHROPIC_API_KEY')]) {
          sh 'RUN_LIVE_EVALS=true jenkins/scripts/run_checks.sh'
        }
      }
      post {
        always { archiveArtifacts allowEmptyArchive: true, artifacts: 'reports/eval-results.json' }
      }
    }

    stage('Build & push to ECR') {
      when { branch 'main' }
      steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding', credentialsId: 'aws-insightly']]) {
          sh 'terraform -chdir=infra init -input=false >/dev/null'
          sh 'jenkins/scripts/build_and_push.sh'
        }
      }
    }

    stage('Approve production') {
      when { branch 'main' }
      steps {
        timeout(time: 30, unit: 'MINUTES') {
          input message: "Deploy ${sh(script: 'cat .image-tag', returnStdout: true).trim()} to EKS?", ok: 'Deploy'
        }
      }
    }

    stage('Deploy to EKS') {
      when { branch 'main' }
      steps {
        withCredentials([[$class: 'AmazonWebServicesCredentialsBinding', credentialsId: 'aws-insightly']]) {
          sh "RUN_INGEST=${params.RUN_INGEST} jenkins/scripts/deploy_eks.sh"
        }
      }
    }
  }
  post {
    failure { echo 'Pipeline failed: check the stage logs above.' }
    cleanup { cleanWs(deleteDirs: true, notFailBuild: true) }
  }
}
