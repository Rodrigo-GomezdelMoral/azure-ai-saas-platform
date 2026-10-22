param issuer string

var audiences = ['api://AzureADTokenExchange']
var gatewayAccounts = [
  { name: 'starter-gateway', subject: 'system:serviceaccount:tenants-starter:gateway' }, { name: 'meter-drain', subject: 'system:serviceaccount:platform:meter-drain' }
]

resource gateway 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = { name: 'id-saasgw' }
resource function 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = { name: 'id-saasfn' }
resource onboard 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = { name: 'id-saasonboard' }

@batchSize(1)
resource gatewayFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = [for account in gatewayAccounts: {
  parent: gateway, name: account.name, properties: { issuer: issuer, subject: account.subject, audiences: audiences }
}]

resource kedaFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: function, name: 'keda-operator', properties: { issuer: issuer, subject: 'system:serviceaccount:kube-system:keda-operator', audiences: audiences }
}

resource onboardFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2024-11-30' = {
  parent: onboard, name: 'onboarding', properties: { issuer: issuer, subject: 'system:serviceaccount:platform:onboarding', audiences: audiences }
}
