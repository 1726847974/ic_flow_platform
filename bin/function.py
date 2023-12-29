# -*- coding: utf-8 -*-
################################
# File Name   : function.py
# Author      : liyanqing
# Created On  : 2022-06-07 20:08:51
# Description :
################################
import os
import re
import sys
import time
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from collections import defaultdict

# Import common python files.
sys.path.append(str(os.environ['IFP_INSTALL_PATH']) + '/common')
import common
import common_lsf


def set_command_env(block='', version='', flow='', vendor='', branch='', task=''):
    if block:
        os.environ['BLOCK'] = block

    if version:
        os.environ['VERSION'] = version

    if flow:
        os.environ['FLOW'] = flow

    if vendor:
        os.environ['VENDOR'] = vendor

    if branch:
        os.environ['BRANCH'] = branch

    if task:
        os.environ['TASK'] = task


class IfpCommon(QThread):
    """
    User customized process.
    """
    def __init__(self, task_list, config_dic, debug=False):
        super().__init__()
        self.task_list = task_list
        self.config_dic = config_dic
        self.debug = debug

    def debug_print(self, message):
        if self.debug:
            print(message)

    def print_output(self, block, version, flow, vendor, branch, task, result, output):
        self.debug_print('')
        self.debug_print('[DEBUG] Block(' + str(block) + ')  Version(' + str(version) + ')  Flow(' + str(flow) + ')  Vendor(' + str(vendor) + ')  Branch(' + str(branch) + ')  Task(' + str(task) + ')  :  ' + str(result))
        self.debug_print('[DEBUG] ----------------')

        try:
            for line in str(output, 'utf-8').split('\n'):
                if line:
                    self.debug_print('[DEBUG] ' + str(line))
        except Exception:
            pass

        self.debug_print('[DEBUG] ----------------')
        self.debug_print('')


class IfpBuild(IfpCommon):
    """
    Build EDA directory and environment.
    """
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def build_one_task(self, item):
        block = item.Block
        version = item.Version
        flow = item.Flow
        vendor = item.Vendor
        branch = item.Branch
        task = item.Task
        result = ''

        # build_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run build_command under branch directory.
        build_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('BUILD', None)

        if build_action and build_action.get('COMMAND'):
            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.building)
            self.msg_signal.emit({'message': '{} {} {} {} {} {} {}'.format(common.status.building, block, version, flow, vendor, branch, task), 'color': 'black'})

            command = build_action['COMMAND']

            if ('PATH' in build_action) and build_action['PATH']:
                if os.path.exists(build_action['PATH']):
                    command = 'cd ' + str(build_action['PATH']) + '; ' + str(command)
                else:
                    self.msg_signal.emit({'message': '*Warning*: {} PATH "'.format(common.status.build) + str(build_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: {} PATH is not defined for task "'.format(common.status.build) + str(task) + '".', 'color': 'orange'})

            (return_code, stdout, stderr) = common.run_command(command)

            if return_code == 0:
                result = '{} {}'.format(common.status.build, common.status.passed)
            else:
                self.msg_signal.emit({'message': '{} {} as: {}'.format(common.status.build, common.status.undefined, stderr.decode('utf-8')), 'color': 'black'})
                result = '{} {}'.format(common.status.build, common.status.failed)

            self.print_output(block, version, flow, vendor, branch, task, result, stdout + stderr)
        else:
            result = '{} {}'.format(common.status.build, common.status.undefined)

        # Tell GUI the build result.
        self.finish_one_signal.emit(block, version, flow, vendor, branch, task, result)

    def run(self):
        self.msg_signal.emit({'message': '>>> {} blocks ...'.format(common.status.building), 'color': 'black'})

        thread_list = []
        build_warning = False

        for item in self.task_list:
            if item.Status in [common.status.running, common.status.killing]:
                build_warning = True
            else:
                thread = threading.Thread(target=self.build_one_task, args=(item,))
                thread.start()
                thread_list.append(thread)

        if build_warning:
            self.msg_signal.emit({'message': '*{} Warning*: Partially selected tasks are either {} or {}, which will not be {}'.format(common.status.build, common.status.running, common.status.killing, common.status.build), 'color': 'orange'})

        # Wait for thread done.
        for thread in thread_list:
            thread.join()

        # Tell GUI build done.
        self.msg_signal.emit({'message': '{} Done.'.format(common.status.build), 'color': 'black'})
        self.finish_signal.emit()


class IfpRun(IfpCommon):
    """
    Run specified task.
    """
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)
    set_one_jobid_signal = pyqtSignal(str, str, str, str, str, str, str, str)
    set_run_time_signal = pyqtSignal(str, str, str, str, str, str, str, str)

    def __init__(self, task_list, config_dic, action='RUN', debug=False, ignore_fail=False):
        super().__init__(task_list, config_dic, debug)

        self.action = action
        self.ignore_fail = ignore_fail
        self.block_version_to_run = list({(x.Block, x.Version): 1 for x in task_list}.keys())
        self.task_groups = self.group_tasks()

    def run_block_version(self):
        block_version_process = []

        for block, version in self.block_version_to_run:
            block_version_tasks = list(filter(lambda x: x.Block == block and x.Version == version, self.task_list))

            if block_version_tasks:
                flow_order = self.config_dic['RUN_ORDER']['{}:{}'.format(block, version)]
                p = threading.Thread(target=self.run_flows, args=(block_version_tasks, flow_order))
                p.start()
                block_version_process.append(p)

        for p in block_version_process:
            p.join()

    def run_flows(self, tasks, flow_order):
        for i, flow_bundle in enumerate(flow_order):
            if i == 0:
                flow_process = []

                for flow in flow_bundle.split('|'):
                    flow_tasks = list(filter(lambda x: x.Flow == flow, tasks))

                    if flow_tasks:
                        p = threading.Thread(target=self.run_flow, args=(flow_tasks,))
                        p.start()
                        flow_process.append(p)

                for p in flow_process:
                    p.join()
            else:
                pre_flows_bundle = flow_order[i-1]
                pre_flows_bundle_tasks = []

                for pre_flow in pre_flows_bundle.split('|'):
                    pre_flow_tasks = list(filter(lambda x: x.Flow == pre_flow, tasks))
                    pre_flows_bundle_tasks.extend(pre_flow_tasks)

                # Cancel next flow if pre flow is "Cancelled" or "Killed".
                if list(filter(lambda x: x.Status in str(common.UNEXPECTED_JOB_STATUS), pre_flows_bundle_tasks)) and not self.ignore_fail:
                    for flow in flow_bundle.split('|'):
                        flow_tasks = list(filter(lambda x: x.Flow == flow, tasks))

                        for t in flow_tasks:
                            self.start_one_signal.emit(t.Block, t.Version, t.Flow, t.Vendor, t.Branch, t.Task, common.status.cancelled)

                    continue

                # Run all tasks.
                flow_process = []

                for flow in flow_bundle.split('|'):
                    flow_tasks = list(filter(lambda x: x.Flow == flow, tasks))

                    if flow_tasks:
                        p = threading.Thread(target=self.run_flow, args=(flow_tasks,))
                        p.start()
                        flow_process.append(p)

                for p in flow_process:
                    p.join()

    def run_flow(self, tasks):
        groups = []

        for t in tasks:
            key = '{}.{}.{}.{}.{}'.format(t.Block, t.Version, t.Flow, t.Vendor, t.Branch)

            if key not in groups:
                groups.append(key)

        group_process = []

        for group in groups:
            run_type = self.config_dic['RUN_TYPE'][group]
            tasks = self.task_groups[group]
            p = threading.Thread(target=self.run_group, args=(tasks, run_type))
            p.start()
            group_process.append(p)

        for p in group_process:
            p.join()

    def run_one_task(self, block, version, flow, vendor, branch, task):
        # run_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run run_command under branch directory.
        run_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get(self.action, None)
        result = ''

        if (not run_action) or (not run_action.get('COMMAND')):
            result = '{} {}'.format(common.status.run, common.status.undefined)
        else:
            # Tell GUI the task run start.
            run_method = run_action.get('RUN_METHOD', '')

            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.running)
            self.msg_signal.emit({'message': '*Info*: {} {} "{}" under {} for {} {} {} {} {} {}\n'.format(common.status.running,
                                                                                                          run_method,
                                                                                                          run_action['PATH'],
                                                                                                          run_action['COMMAND'],
                                                                                                          block,
                                                                                                          version,
                                                                                                          flow,
                                                                                                          vendor,
                                                                                                          branch,
                                                                                                          task),
                                  'color': 'black'})

            # if run_method without -I option
            if re.search('bsub', run_method) and (not re.search('-I', run_method)):
                run_method = run_method + ' -I '

            # Get command
            command = run_action['COMMAND']

            if (not re.search(r'^\s*$', run_method)) and (not re.search(r'^\s*local\s*$', run_method, re.I)):
                command = str(run_method) + ' "' + str(command) + '"'

            if ('PATH' in run_action) and run_action['PATH']:
                if os.path.exists(run_action['PATH']):
                    command = 'cd ' + str(run_action['PATH']) + '; ' + str(command)
                else:
                    self.msg_signal.emit({'message': '*Warning*: RUN PATH "' + str(run_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: RUN PATH is not defined for task "' + str(task) + '".', 'color': 'orange'})

            # Run command
            if re.search(r'^\s*bsub', run_method):
                process = common.spawn_process(command)
                stdout = process.stdout.readline().decode('utf-8')
                jobid = 'b:{}'.format(common.get_jobid(stdout))

                self.set_one_jobid_signal.emit(block, version, flow, vendor, branch, task, 'Job', str(jobid))
                self.set_run_time_signal.emit(block, version, flow, vendor, branch, task, 'Runtime', "pending")

                while (True):
                    current_job = jobid[2:]
                    current_job_dic = common_lsf.get_bjobs_uf_info(command='bjobs -UF ' + str(current_job))

                    if current_job_dic:
                        job_status = current_job_dic[current_job]['status']

                        if job_status == "RUN":
                            self.set_run_time_signal.emit(block, version, flow, vendor, branch, task, 'Runtime', "00:00:00")
                            break

                    time.sleep(1)
            else:
                process = common.spawn_process(command)
                jobid = 'l:{}'.format(process.pid)

                self.set_one_jobid_signal.emit(block, version, flow, vendor, branch, task, 'Job', str(jobid))
                self.set_run_time_signal.emit(block, version, flow, vendor, branch, task, 'Runtime', "00:00:00")

            stdout, stderr = process.communicate()
            return_code = process.returncode

            last_status = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task].get('Status', None)

            if last_status == common.status.killing:
                if str(jobid).startswith('b'):
                    jobid = str(jobid)[2:]

                while True:
                    time.sleep(3)
                    bjobs_dic = common_lsf.get_bjobs_info('bjobs ' + str(jobid))

                    if ('STAT' in bjobs_dic.keys()) and bjobs_dic['STAT'] and (bjobs_dic['STAT'][0] == 'EXIT'):
                        result = common.status.killed
                        self.msg_signal.emit({'message': '*Info*: job killed for {} {} {} {} {} {}\n'.format(block, version, flow, vendor, branch, task), 'color': 'black'})
                        break
            elif last_status == common.status.killed:
                result = common.status.killed
                self.msg_signal.emit({'message': '*Info*: job killed for {} {} {} {} {} {}\n'.format(block, version, flow, vendor, branch, task), 'color': 'black'})
            else:
                if return_code == 0:
                    result = '{} {}'.format(common.status.run, common.status.passed)
                else:
                    result = '{} {}'.format(common.status.run, common.status.failed)

                self.print_output(block, version, flow, vendor, branch, task, result, stdout + stderr)
                self.msg_signal.emit({'message': '*Info*: job done for {} {} {} {} {} {}\n'.format(block, version, flow, vendor, branch, task), 'color': 'black'})

        # Tell GUI the task run finish.
        self.config_dic['BLOCK'][block][version][flow][vendor][branch][task].Status = result

        # Tell GUI the run result.
        self.finish_one_signal.emit(block, version, flow, vendor, branch, task, result)

    def group_tasks(self):
        """
        group tasks according to RUN_TYPE
        """
        groups = defaultdict(list)

        for task in self.task_list:
            key = '{}.{}.{}.{}.{}'.format(task.Block, task.Version, task.Flow, task.Vendor, task.Branch)
            groups[key].append((task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))

        return groups

    def run_group(self, tasks, run_type):
        if run_type == 'serial':
            for i, t in enumerate(tasks):
                block, version, flow, vendor, branch, task = t

                if i > 0:
                    pre_block, pre_version, pre_flow, pre_vendor, pre_branch, pre_task = tasks[i - 1]
                    pre_task_obj = self.config_dic['BLOCK'][pre_block][pre_version][pre_flow][pre_vendor][pre_branch][pre_task]

                    if (pre_task_obj.get('Status') == '{} {}'.format(common.status.run, common.status.passed)) or self.ignore_fail:
                        self.run_one_task(block, version, flow, vendor, branch, task)

                    if (pre_task_obj.get('Status') in str(common.UNEXPECTED_JOB_STATUS)) and (not self.ignore_fail):
                        self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.cancelled)
                        self.config_dic['BLOCK'][block][version][flow][vendor][branch][task].Status = common.status.cancelled
                else:
                    current_task_obj = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]

                    if current_task_obj.get('Status') in [common.status.running, common.status.killing]:
                        while current_task_obj.get('Status') in [common.status.running, common.status.killing]:
                            time.sleep(5)
                    else:
                        self.run_one_task(block, version, flow, vendor, branch, task)

        elif run_type == 'parallel':
            thread_list = []

            for t in tasks:
                block, version, flow, vendor, branch, task = t
                current_task_obj = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]

                if current_task_obj.get('Status') not in [common.status.running, common.status.killing]:
                    thread = threading.Thread(target=self.run_one_task, args=(block, version, flow, vendor, branch, task))
                    thread.start()
                    thread_list.append(thread)

            for t in thread_list:
                t.join()

    def run(self):

        msg_flag = 0

        # Set all tasks queued
        for task in self.task_list:
            if task.Status not in [common.status.running, common.status.killing]:
                msg_flag = 1
                task.Status = common.status.queued
                self.start_one_signal.emit(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task, common.status.queued)
                self.set_run_time_signal.emit(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task, 'Runtime', None)

        if msg_flag:
            if self.action == 'RUN':
                self.msg_signal.emit({'message': '>>> {} tasks ...'.format(common.status.running), 'color': 'black'})

        self.run_block_version()

        # Tell GUI run done.
        self.finish_signal.emit()


class IfpKill(IfpCommon):
    """
    With distributed mode, this class is used to kill related jobs.
    """
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def kill_one_task(self, item):
        block = item.Block
        version = item.Version
        flow = item.Flow
        vendor = item.Vendor
        branch = item.Branch
        task = item.Task
        jobid = item.Job
        status = item.Status

        if status == common.status.running:
            self.msg_signal.emit({'message': '{} {} {} {} {} {} {}'.format(common.status.killing, block, version, flow, vendor, branch, task), 'color': 'black'})
            self.config_dic['BLOCK'][block][version][flow][vendor][branch][task].Status = common.status.killing
            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.killing)

            if str(jobid).startswith('b'):
                jobid = str(jobid)[2:]
                common.run_command('bkill ' + str(jobid))
            elif str(jobid).startswith('l'):
                jobid = str(jobid)[2:]
                common.kill_pid_tree(jobid)

                self.config_dic['BLOCK'][block][version][flow][vendor][branch][task].Status = common.status.killed
                self.finish_one_signal.emit(block, version, flow, vendor, branch, task, common.status.killed)

    def run(self):
        for item in self.task_list:
            self.kill_one_task(item)


class IfpCheck(IfpCommon):
    """
    This calss is used to check task result.
    """
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def check_one_task(self, block, version, flow, vendor, branch, task):
        # check_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run check_command under branch directory.
        check_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('CHECK', None)
        result = ''

        if check_action and check_action.get('COMMAND'):
            # Tell GUI the task check start.
            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.checking)

            command = check_action.get('COMMAND')

            if ('PATH' in check_action) and check_action['PATH']:
                if os.path.exists(check_action['PATH']):
                    command = 'cd ' + str(check_action['PATH']) + '; ' + str(command)
                else:
                    self.msg_signal.emit({'message': '*Warning*: {} PATH "'.format(common.status.check) + str(check_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: {} PATH is not defined for task "'.format(common.status.check) + str(task) + '".', 'color': 'orange'})

            return_code, stdout, stderr = common.run_command(command)

            if return_code == 0:
                result = '{} {}'.format(common.status.check, common.status.passed)
            else:
                result = '{} {}'.format(common.status.check, common.status.failed)

            self.print_output(block, version, flow, vendor, branch, task, result, stdout + stderr)
        else:
            result = '{} {}'.format(common.status.check, common.status.undefined)

        # Tell GUI the check check result.
        self.finish_one_signal.emit(block, version, flow, vendor, branch, task, result)

    def run(self):
        self.msg_signal.emit({'message': '>>> {} results ...'.format(common.status.checking), 'color': 'black'})

        thread_list = []

        for task in self.task_list:
            thread = threading.Thread(target=self.check_one_task, args=(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))
            thread_list.append(thread)
            thread.start()

        for thread in thread_list:
            thread.join()

        self.msg_signal.emit({'message': '>>> {} Done'.format(common.status.check), 'color': 'black'})

        # Tell GUI check done.
        self.finish_signal.emit()


class IfpCheckView(IfpCommon):
    """
    This calss is used to view checklist result.
    """
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def check_one_task(self, block, version, flow, vendor, branch, task):
        # check_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run viewer command under task check directory.
        check_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('CHECK', None)
        command = ''

        if check_action:
            if ('PATH' in check_action) and check_action['PATH']:
                if os.path.exists(check_action['PATH']):
                    command = 'cd ' + str(check_action['PATH']) + ';'
                else:
                    self.msg_signal.emit({'message': '*Warning*: Check PATH "' + str(check_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: Check PATH is not defined for task "' + str(task) + '".', 'color': 'orange'})

            if ('VIEWER' in check_action) and check_action['VIEWER']:
                if ('REPORT_FILE' in check_action) and check_action['REPORT_FILE']:
                    if (os.path.exists(check_action['REPORT_FILE'])) or (os.path.exists(str(check_action['PATH']) + '/' + str(check_action['REPORT_FILE']))):
                        command = str(command) + ' ' + str(check_action['VIEWER']) + ' ' + str(check_action['REPORT_FILE'])
                        common.run_command(command)
                    else:
                        if not re.match('^/.*$', check_action['REPORT_FILE']):
                            self.msg_signal.emit({'message': '      *Error*: Check REPORT_FILE "{}/{}" not exists.'.format(check_action['PATH'], check_action['REPORT_FILE']), 'color': 'red'})
                        else:
                            self.msg_signal.emit({'message': '      *Error*: Check REPORT_FILE "{}" not exists.'.format(check_action['REPORT_FILE']), 'color': 'red'})
                else:
                    self.msg_signal.emit({'message': '*Error*: Check REPORT_FILE is not defined for task "' + str(task) + '".', 'color': 'red'})
            else:
                self.msg_signal.emit({'message': '*Error*: Check VIEWER is not defined for task "' + str(task) + '".', 'color': 'red'})

    def run(self):
        thread_list = []

        for task in self.task_list:
            thread = threading.Thread(target=self.check_one_task, args=(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))
            thread_list.append(thread)
            thread.start()

        for thread in thread_list:
            thread.join()


class IfpSummary(IfpCommon):
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def sum_one_task(self, block, version, flow, vendor, branch, task):
        # check_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run check_command under branch directory.
        sum_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('SUMMARY', None)

        if sum_action and sum_action.get('COMMAND'):
            # Tell GUI the task check start.
            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.summarizing)

            command = sum_action.get('COMMAND')

            if ('PATH' in sum_action) and sum_action['PATH']:
                if os.path.exists(sum_action['PATH']):
                    command = 'cd ' + str(sum_action['PATH']) + '; ' + str(command)
                else:
                    self.msg_signal.emit({'message': '*Warning*: {} PATH "'.format(common.status.summarize) + str(sum_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: {} PATH is not defined for task "'.format(common.status.summarize) + str(task) + '".', 'color': 'orange'})

            (return_code, stdout, stderr) = common.run_command(command)

            if return_code == 0:
                result = '{} {}'.format(common.status.summarize, common.status.passed)
            else:
                result = '{} {}'.format(common.status.summarize, common.status.failed)

            self.print_output(block, version, flow, vendor, branch, task, result, stdout + stderr)
        else:
            result = '{} {}'.format(common.status.summarize, common.status.undefined)

        # Tell GUI the check summary result.
        self.finish_one_signal.emit(block, version, flow, vendor, branch, task, result)

    def run(self):
        self.msg_signal.emit({'message': '>>> {} results ...'.format(common.status.summarizing), 'color': 'black'})

        thread_list = []

        for task in self.task_list:
            thread = threading.Thread(target=self.sum_one_task, args=(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))
            thread_list.append(thread)
            thread.start()

        for thread in thread_list:
            thread.join()

        self.msg_signal.emit({'message': '>>> {} Done'.format(common.status.summarize), 'color': 'black'})

        # Tell GUI check done.
        self.finish_signal.emit()


class IfpSummaryView(IfpCommon):
    """
    This calss is used to view summary result.
    """
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def sum_one_task(self, block, version, flow, vendor, branch, task):
        # sum_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run viewer command under task summary directory.
        sum_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('SUMMARY', None)

        if sum_action:
            if ('PATH' in sum_action) and sum_action['PATH']:
                if os.path.exists(sum_action['PATH']):
                    command = 'cd ' + str(sum_action['PATH']) + ';'
                else:
                    self.msg_signal.emit({'message': '*Warning*: Summary PATH "' + str(sum_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: Summary PATH is not defined for task "' + str(task) + '".', 'color': 'orange'})

            if ('VIEWER' in sum_action) and sum_action['VIEWER']:
                if ('REPORT_FILE' in sum_action) and sum_action['REPORT_FILE']:
                    if (os.path.exists(sum_action['REPORT_FILE'])) or (os.path.exists(str(sum_action['PATH']) + '/' + str(sum_action['REPORT_FILE']))):
                        command = str(command) + ' ' + str(sum_action['VIEWER']) + ' ' + str(sum_action['REPORT_FILE'])
                        common.run_command(command)
                    else:
                        if not re.match('^/.*$', sum_action['REPORT_FILE']):
                            self.msg_signal.emit({'message': '      *Error*: Summary REPORT_FILE "{}/{}" not exists.'.format(sum_action['PATH'], sum_action['REPORT_FILE']), 'color': 'red'})
                        else:
                            self.msg_signal.emit({'message': '      *Error*: Summary REPORT_FILE "{}" not exists.'.format(sum_action['REPORT_FILE']), 'color': 'red'})
                else:
                    self.msg_signal.emit({'message': '*Error*: Summary REPORT_FILE is not defined for task "' + str(task) + '".', 'color': 'red'})
            else:
                self.msg_signal.emit({'message': '*Error*: Summary VIEWER is not defined for task "' + str(task) + '".', 'color': 'red'})

    def run(self):
        thread_list = []

        for task in self.task_list:
            thread = threading.Thread(target=self.sum_one_task, args=(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))
            thread_list.append(thread)
            thread.start()

        for thread in thread_list:
            thread.join()


class IfpRelease(IfpCommon):
    """
    This class is used to release if needed.
    """
    start_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_one_signal = pyqtSignal(str, str, str, str, str, str, str)
    finish_signal = pyqtSignal()
    msg_signal = pyqtSignal(dict)

    def __init__(self, task_list, config_dic, debug=False):
        super().__init__(task_list, config_dic, debug)

    def release_one_task(self, block, version, flow, vendor, branch, task):
        # check_command can read and use these information.
        set_command_env(block, version, flow, vendor, branch, task)

        # Run check_command under branch directory.
        release_action = self.config_dic['BLOCK'][block][version][flow][vendor][branch][task]['ACTION'].get('RELEASE', None)

        if release_action and release_action.get('COMMAND'):
            # Tell GUI the task check start.
            self.start_one_signal.emit(block, version, flow, vendor, branch, task, common.status.releasing)

            command = release_action.get('COMMAND')

            if ('PATH' in release_action) and release_action['PATH']:
                if os.path.exists(release_action['PATH']):
                    command = 'cd ' + str(release_action['PATH']) + '; ' + str(command)
                else:
                    self.msg_signal.emit({'message': '*Warning*: {} PATH "'.format(common.status.release) + str(release_action['PATH']) + '" not exists.', 'color': 'orange'})
            else:
                self.msg_signal.emit({'message': '*Warning*: {} PATH is not defined for task "'.format(common.status.release) + str(task) + '".', 'color': 'orange'})

            (return_code, stdout, stderr) = common.run_command(command)

            if return_code == 0:
                result = '{} {}'.format(common.status.release, common.status.passed)
            else:
                result = '{} {}'.format(common.status.release, common.status.failed)

            self.print_output(block, version, flow, vendor, branch, task, result, stdout + stderr)
        else:
            result = '{} {}'.format(common.status.release, common.status.undefined)

        # Tell GUI the check release result.
        self.finish_one_signal.emit(block, version, flow, vendor, branch, task, result)

    def run(self):
        self.msg_signal.emit({'message': '>>> {}...'.format(common.status.releasing), 'color': 'black'})

        thread_list = []

        for task in self.task_list:
            thread = threading.Thread(target=self.release_one_task, args=(task.Block, task.Version, task.Flow, task.Vendor, task.Branch, task.Task))
            thread_list.append(thread)
            thread.start()

        for thread in thread_list:
            thread.join()

        self.msg_signal.emit({'message': '>>> {} Done'.format(common.status.release), 'color': 'black'})

        # Tell GUI check done.
        self.finish_signal.emit()
