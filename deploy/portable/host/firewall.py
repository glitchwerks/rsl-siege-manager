#!/usr/bin/python3
"""Own only RSL-HOST/RSL-CONTAINERS; preserve Docker and unrelated firewall rules."""
import subprocess,sys

def run(binary,*args,check=True):
 return subprocess.run([binary,'-w','5',*args],check=check,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
def present(binary,*args):return run(binary,*args,check=False).returncode==0
mode=sys.argv[1];assert mode in {'apply','remove'}
for binary in ['/usr/sbin/iptables','/usr/sbin/ip6tables']:
 if mode=='remove':
  while present(binary,'-C','INPUT','-j','RSL-HOST'):run(binary,'-D','INPUT','-j','RSL-HOST')
  while present(binary,'-C','DOCKER-USER','-i','eth0','-j','RSL-CONTAINERS'):run(binary,'-D','DOCKER-USER','-i','eth0','-j','RSL-CONTAINERS')
  for chain in ['RSL-HOST','RSL-CONTAINERS']:
   if present(binary,'-S',chain):run(binary,'-F',chain);run(binary,'-X',chain)
  continue
 for chain in ['RSL-HOST','RSL-CONTAINERS']:
  if not present(binary,'-S',chain):run(binary,'-N',chain)
  run(binary,'-F',chain)
 run(binary,'-A','RSL-HOST','-i','lo','-j','ACCEPT')
 run(binary,'-A','RSL-HOST','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT')
 run(binary,'-A','RSL-HOST','-p','ipv6-icmp' if binary.endswith('ip6tables') else 'icmp','-j','ACCEPT')
 run(binary,'-A','RSL-HOST','-p','tcp','-m','multiport','--dports','22,80,443','-j','ACCEPT')
 run(binary,'-A','RSL-HOST','-j','DROP')
 if not present(binary,'-C','INPUT','-j','RSL-HOST'):run(binary,'-I','INPUT','1','-j','RSL-HOST')
 # Docker-published ports traverse FORWARD, not the host INPUT chain.
 if not present(binary,'-S','DOCKER-USER'):run(binary,'-N','DOCKER-USER')
 if not present(binary,'-C','FORWARD','-j','DOCKER-USER'):run(binary,'-I','FORWARD','1','-j','DOCKER-USER')
 run(binary,'-A','RSL-CONTAINERS','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','RETURN')
 for port in ['80','443']:
  run(binary,'-A','RSL-CONTAINERS','-p','tcp','-m','conntrack','--ctorigdstport',port,'-j','RETURN')
 run(binary,'-A','RSL-CONTAINERS','-j','DROP')
 if not present(binary,'-C','DOCKER-USER','-i','eth0','-j','RSL-CONTAINERS'):run(binary,'-I','DOCKER-USER','1','-i','eth0','-j','RSL-CONTAINERS')
print('RSL firewall '+mode+' completed.')
