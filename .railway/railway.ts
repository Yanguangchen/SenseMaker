import { defineRailway, github, project, service } from "railway/iac";

export default defineRailway(() => {
  const web = service("sensemaker", {
    source: github("Yanguangchen/SenseMaker", { branch: "main" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    healthcheck: "/_stcore/health",
    healthcheckTimeout: 120,
    replicas: 1,
    deploy: {
      sleepApplication: false,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
  });

  // Credentials are managed through Railway Variables, outside source control.
  // The CLI's variables policy preserves them when applying infrastructure.
  const definition = {
    variables: { managed: false },
    resources: [web],
  };
  return project("SenseMaker", definition);
});
