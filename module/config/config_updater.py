import re
import typing as t
from copy import deepcopy

from cached_property import cached_property

from deploy.Windows.utils import DEPLOY_TEMPLATE, poor_yaml_read, poor_yaml_write
from module.base.timer import timer
from module.config.env import IS_ON_PHONE_CLOUD
from module.config.redirect_utils.utils import *
from module.config.server import VALID_CHANNEL_PACKAGE, VALID_PACKAGE, VALID_SERVER_LIST, to_package, to_server
from module.config.utils import *

CONFIG_IMPORT = '''
import datetime

# This file was automatically generated by module/config/config_updater.py.
# Don't modify it manually.


class GeneratedConfig:
    """
    Auto generated configuration
    """
'''.strip().split('\n')
ARCHIVES_PREFIX = {
    'cn': '档案 ',
    'en': 'archives ',
    'jp': '檔案 ',
    'tw': '檔案 '
}
MAINS = ['Main', 'Main2', 'Main3']
EVENTS = ['Event', 'Event2', 'Event3', 'EventA', 'EventB', 'EventC', 'EventD', 'EventSp']
GEMS_FARMINGS = ['GemsFarming']
RAIDS = ['Raid', 'RaidDaily']
WAR_ARCHIVES = ['WarArchives']
COALITIONS = ['Coalition', 'CoalitionSp']
MARITIME_ESCORTS = ['MaritimeEscort']


class Event:
    def __init__(self, text):
        self.date, self.directory, self.name, self.cn, self.en, self.jp, self.tw \
            = [x.strip() for x in text.strip('| \n').split('|')]

        self.directory = self.directory.replace(' ', '_')
        self.cn = self.cn.replace('、', '')
        self.en = self.en.replace(',', '').replace('\'', '').replace('\\', '')
        self.jp = self.jp.replace('、', '')
        self.tw = self.tw.replace('、', '')
        self.is_war_archives = self.directory.startswith('war_archives')
        self.is_raid = self.directory.startswith('raid_')
        self.is_coalition = self.directory.startswith('coalition_')
        for server in ARCHIVES_PREFIX.keys():
            if self.__getattribute__(server) == '-':
                self.__setattr__(server, None)
            else:
                if self.is_war_archives:
                    self.__setattr__(server, ARCHIVES_PREFIX[server] + self.__getattribute__(server))

    def __str__(self):
        return self.directory

    def __eq__(self, other):
        return str(self) == str(other)

    def __lt__(self, other):
        return str(self) < str(other)

    def __hash__(self):
        return hash(str(self))


class ConfigGenerator:
    @cached_property
    def argument(self):
        """
        Load argument.yaml, and standardise its structure.

        <group>:
            <argument>:
                type: checkbox|select|textarea|input
                value:
                option (Optional): Options, if argument has any options.
                validate (Optional): datetime
        """
        data = {}
        raw = read_file(filepath_argument('argument'))
        for path, value in deep_iter(raw, depth=2):
            arg = {
                'type': 'input',
                'value': '',
                # option
            }
            if not isinstance(value, dict):
                value = {'value': value}
            arg['type'] = data_to_type(value, arg=path[1])
            if isinstance(value['value'], datetime):
                arg['type'] = 'datetime'
                arg['validate'] = 'datetime'
            # Manual definition has the highest priority
            arg.update(value)
            deep_set(data, keys=path, value=arg)

        # Define storage group
        arg = {
            'type': 'storage',
            'value': {},
            'valuetype': 'ignore',
            'display': 'disabled',
        }
        deep_set(data, keys=['Storage', 'Storage'], value=arg)
        return data

    @cached_property
    def task(self):
        """
        <task_group>:
            <task>:
                <group>:
        """
        return read_file(filepath_argument('task'))

    @cached_property
    def default(self):
        """
        <task>:
            <group>:
                <argument>: value
        """
        return read_file(filepath_argument('default'))

    @cached_property
    def override(self):
        """
        <task>:
            <group>:
                <argument>: value
        """
        return read_file(filepath_argument('override'))

    @cached_property
    def gui(self):
        """
        <i18n_group>:
            <i18n_key>: value, value is None
        """
        return read_file(filepath_argument('gui'))

    @cached_property
    def dashboard(self):
        """
        <dashboard>
          - <group>
        """
        return read_file(filepath_argument('dashboard'))


    @cached_property
    @timer
    def args(self):
        """
        Merge definitions into standardised json.

            task.yaml ---+
        argument.yaml ---+-----> args.json
        override.yaml ---+
         default.yaml ---+

        """
        # Construct args
        data = {}
        # Add dashboard to args
        dashboard_and_task = {**self.task, **self.dashboard}
        for path, groups in deep_iter(dashboard_and_task, depth=3):
            if 'tasks' not in path and 'Dashboard' not in path:
                continue
            task = path[2] if 'tasks' in path else path[0]
            # Add storage to all task
            groups.append('Storage')
            for group in groups:
                if group not in self.argument:
                    print(f'`{task}.{group}` is not related to any argument group')
                    continue
                deep_set(data, keys=[task, group], value=deepcopy(self.argument[group]))

        def check_override(path, value):
            # Check existence
            old = deep_get(data, keys=path, default=None)
            if old is None:
                print(f'`{".".join(path)}` is not a existing argument')
                return False
            # Check type
            # But allow `Interval` to be different
            old_value = old.get('value', None) if isinstance(old, dict) else old
            value = old.get('value', None) if isinstance(value, dict) else value
            if type(value) != type(old_value) \
                    and old_value is not None \
                    and path[2] not in ['SuccessInterval', 'FailureInterval']:
                print(
                    f'`{value}` ({type(value)}) and `{".".join(path)}` ({type(old_value)}) are in different types')
                return False
            # Check option
            if isinstance(old, dict) and 'option' in old:
                if value not in old['option']:
                    print(f'`{value}` is not an option of argument `{".".join(path)}`')
                    return False
            return True

        # Set defaults
        for p, v in deep_iter(self.default, depth=3):
            if not check_override(p, v):
                continue
            deep_set(data, keys=p + ['value'], value=v)
        # Override non-modifiable arguments
        for p, v in deep_iter(self.override, depth=3):
            if not check_override(p, v):
                continue
            if isinstance(v, dict):
                typ = v.get('type')
                if typ == 'state':
                    pass
                elif typ == 'lock':
                    pass
                elif deep_get(v, keys='value') is not None:
                    deep_default(v, keys='display', value='hide')
                for arg_k, arg_v in v.items():
                    deep_set(data, keys=p + [arg_k], value=arg_v)
            else:
                deep_set(data, keys=p + ['value'], value=v)
                deep_set(data, keys=p + ['display'], value='hide')
        # Set command
        for path, groups in deep_iter(self.task, depth=3):
            if 'tasks' not in path:
                continue
            task = path[2]
            if deep_get(data, keys=f'{task}.Scheduler.Command'):
                deep_set(data, keys=f'{task}.Scheduler.Command.value', value=task)
                deep_set(data, keys=f'{task}.Scheduler.Command.display', value='hide')

        return data

    @timer
    def generate_code(self):
        """
        Generate python code.

        args.json ---> config_generated.py

        """
        visited_group = set()
        visited_path = set()
        lines = CONFIG_IMPORT
        for path, data in deep_iter(self.argument, depth=2):
            group, arg = path
            if group not in visited_group:
                lines.append('')
                lines.append(f'    # Group `{group}`')
                visited_group.add(group)

            option = ''
            if 'option' in data and data['option']:
                option = '  # ' + ', '.join([str(opt) for opt in data['option']])
            path = '.'.join(path)
            lines.append(f'    {path_to_arg(path)} = {repr(parse_value(data["value"], data=data))}{option}')
            visited_path.add(path)

        with open(filepath_code(), 'w', encoding='utf-8', newline='') as f:
            for text in lines:
                f.write(text + '\n')

    @timer
    def generate_i18n(self, lang):
        """
        Load old translations and generate new translation file.

                     args.json ---+-----> i18n/<lang>.json
        (old) i18n/<lang>.json ---+

        """
        new = {}
        old = read_file(filepath_i18n(lang))

        def deep_load(keys, default=True, words=('name', 'help')):
            for word in words:
                k = keys + [str(word)]
                d = ".".join(k) if default else str(word)
                v = deep_get(old, keys=k, default=d)
                deep_set(new, keys=k, value=v)

        # Menu
        for path, data in deep_iter(self.task, depth=3):
            if 'tasks' not in path:
                continue
            task_group, _, task = path
            deep_load(['Menu', task_group])
            deep_load(['Task', task])
        # Arguments
        visited_group = set()
        for path, data in deep_iter(self.argument, depth=2):
            if path[0] not in visited_group:
                deep_load([path[0], '_info'])
                visited_group.add(path[0])
            deep_load(path)
            if 'option' in data:
                deep_load(path, words=data['option'], default=False)
        # Event names
        # Names come from SameLanguageServer > en > cn > jp > tw
        events = {}
        for event in self.event:
            if lang in LANG_TO_SERVER:
                name = event.__getattribute__(LANG_TO_SERVER[lang])
                if name:
                    deep_default(events, keys=event.directory, value=name)
        for server in ['en', 'cn', 'jp', 'tw']:
            for event in self.event:
                name = event.__getattribute__(server)
                if name:
                    deep_default(events, keys=event.directory, value=name)
        for event in sorted(self.event):
            name = events.get(event.directory, event.directory)
            deep_set(new, keys=f'Campaign.Event.{event.directory}', value=name)
        # Package names
        for package, server in VALID_PACKAGE.items():
            path = ['Emulator', 'PackageName', package]
            if deep_get(new, keys=path) == package:
                deep_set(new, keys=path, value=server.upper())

        for package, server_and_channel in VALID_CHANNEL_PACKAGE.items():
            server, channel = server_and_channel
            name = deep_get(new, keys=['Emulator', 'PackageName', to_package(server)])
            if lang == SERVER_TO_LANG[server]:
                value = f'{name} {channel}渠道服 {package}'
            else:
                value = f'{name} {package}'
            deep_set(new, keys=['Emulator', 'PackageName', package], value=value)
        # Game server names
        for server, _list in VALID_SERVER_LIST.items():
            for index in range(len(_list)):
                path = ['Emulator', 'ServerName', f'{server}-{index}']
                prefix = server.split('_')[0].upper()
                prefix = '国服' if prefix == 'CN' else prefix
                deep_set(new, keys=path, value=f'[{prefix}] {_list[index]}')
        # GUI i18n
        for path, _ in deep_iter(self.gui, depth=2):
            group, key = path
            deep_load(keys=['Gui', group], words=(key,))
        # zh-TW
        dic_repl = {
            '設置': '設定',
            '支持': '支援',
            '啓': '啟',
            '异': '異',
            '服務器': '伺服器',
            '文件': '檔案',
        }
        if lang == 'zh-TW':
            for path, value in deep_iter(new, depth=3):
                for before, after in dic_repl.items():
                    value = value.replace(before, after)
                deep_set(new, keys=path, value=value)

        write_file(filepath_i18n(lang), new)

    @cached_property
    def menu(self):
        """
        Generate menu definitions

        task.yaml --> menu.json

        """
        data = {}
        for task_group in self.task.keys():
            value = deep_get(self.task, keys=[task_group, 'menu'])
            if value not in ['collapse', 'list']:
                value = 'collapse'
            deep_set(data, keys=[task_group, 'menu'], value=value)
            value = deep_get(self.task, keys=[task_group, 'page'])
            if value not in ['setting', 'tool']:
                value = 'setting'
            deep_set(data, keys=[task_group, 'page'], value=value)
            tasks = deep_get(self.task, keys=[task_group, 'tasks'], default={})
            tasks = list(tasks.keys())
            deep_set(data, keys=[task_group, 'tasks'], value=tasks)

        return data

    @cached_property
    @timer
    def event(self):
        """
        Returns:
            list[Event]: From latest to oldest
        """
        events = []
        with open('./campaign/Readme.md', encoding='utf-8') as f:
            for text in f.readlines():
                if re.search(r'\d{8}', text):
                    event = Event(text)
                    events.append(event)

        return events[::-1]

    def insert_event(self):
        """
        Insert event information into `self.args`.

        ./campaign/Readme.md -----+
                                  v
                   args.json -----+-----> args.json
        """
        for event in self.event:
            for server in ARCHIVES_PREFIX.keys():
                name = event.__getattribute__(server)

                def insert(key):
                    opts = deep_get(self.args, keys=f'{key}.Campaign.Event.option')
                    if event not in opts:
                        opts.append(event)
                    if name:
                        deep_default(self.args, keys=f'{key}.Campaign.Event.{server}', value=event)

                if name:
                    if event.is_raid:
                        for task in RAIDS:
                            insert(task)
                    elif event.is_war_archives:
                        for task in WAR_ARCHIVES:
                            insert(task)
                    elif event.is_coalition:
                        for task in COALITIONS:
                            insert(task)
                    else:
                        for task in EVENTS + GEMS_FARMINGS:
                            insert(task)

        for task in EVENTS + GEMS_FARMINGS + WAR_ARCHIVES + RAIDS + COALITIONS:
            options = deep_get(self.args, keys=f'{task}.Campaign.Event.option')
            # Remove campaign_main from event list
            options = [option for option in options if option != 'campaign_main']
            # Sort options
            options = sorted(options)
            deep_set(self.args, keys=f'{task}.Campaign.Event.option', value=options)
            # Sort latest
            latest = {}
            for server in ARCHIVES_PREFIX.keys():
                latest[server] = deep_pop(self.args, keys=f'{task}.Campaign.Event.{server}', default='')
            bold = sorted(set(latest.values()))
            deep_set(self.args, keys=f'{task}.Campaign.Event.option_bold', value=bold)
            for server, event in latest.items():
                deep_set(self.args, keys=f'{task}.Campaign.Event.{server}', value=event)

    @staticmethod
    def generate_deploy_template():
        template = poor_yaml_read(DEPLOY_TEMPLATE)
        cn = {
            'Repository': 'git://git.lyoko.io/AzurLaneAutoScript',
            'PypiMirror': 'https://pypi.tuna.tsinghua.edu.cn/simple',
            'Language': 'zh-CN',
        }
        aidlux = {
            'GitExecutable': '/usr/bin/git',
            'PythonExecutable': '/usr/bin/python',
            'RequirementsFile': './deploy/AidLux/0.92/requirements.txt',
            'AdbExecutable': '/usr/bin/adb',
        }

        docker = {
            'GitExecutable': '/usr/bin/git',
            'PythonExecutable': '/usr/local/bin/python',
            'RequirementsFile': './deploy/docker/requirements.txt',
            'AdbExecutable': '/usr/bin/adb',
        }

        linux = {
            'GitExecutable': '/usr/bin/git',
            'PythonExecutable': 'python',
            'RequirementsFile': './deploy/headless/requirements.txt',
            'AdbExecutable': '/usr/bin/adb',
            'SSHExecutable': '/usr/bin/ssh',
            'ReplaceAdb': 'false'
        }

        def update(suffix, *args):
            file = f'./config/deploy.{suffix}.yaml'
            new = deepcopy(template)
            for dic in args:
                new.update(dic)
            poor_yaml_write(data=new, file=file)

        update('template')
        update('template-cn', cn)
        update('template-AidLux', aidlux)
        update('template-AidLux-cn', aidlux, cn)
        update('template-docker', docker)
        update('template-docker-cn', docker, cn)
        update('template-linux', linux)
        update('template-linux-cn', linux, cn)

        tpl = {
            'Repository': '{{repository}}',
            'GitExecutable': '{{gitExecutable}}',
            'PythonExecutable': '{{pythonExecutable}}',
            'AdbExecutable': '{{adbExecutable}}',
            'Language': '{{language}}',
            'Theme': '{{theme}}',
        }
        def update(file, *args):
            new = deepcopy(template)
            for dic in args:
                new.update(dic)
            poor_yaml_write(data=new, file=file)

        update('./webapp/packages/main/public/deploy.yaml.tpl', tpl)

    def insert_package(self):
        option = deep_get(self.argument, keys='Emulator.PackageName.option')
        option += list(VALID_PACKAGE.keys())
        option += list(VALID_CHANNEL_PACKAGE.keys())
        deep_set(self.argument, keys='Emulator.PackageName.option', value=option)
        deep_set(self.args, keys='Alas.Emulator.PackageName.option', value=option)

    def insert_server(self):
        option = deep_get(self.argument, keys='Emulator.ServerName.option')
        server_list = []
        for server, _list in VALID_SERVER_LIST.items():
            for index in range(len(_list)):
                server_list.append(f'{server}-{index}')
        option += server_list
        deep_set(self.argument, keys='Emulator.ServerName.option', value=option)
        deep_set(self.args, keys='Alas.Emulator.ServerName.option', value=option)

    @timer
    def generate(self):
        _ = self.args
        _ = self.menu
        _ = self.event
        self.insert_event()
        self.insert_package()
        self.insert_server()
        write_file(filepath_args(), self.args)
        write_file(filepath_args('menu'), self.menu)
        self.generate_code()
        for lang in LANGUAGES:
            self.generate_i18n(lang)
        self.generate_deploy_template()


class ConfigUpdater:
    # source, target, (optional)convert_func
    redirection = [
        # ('OpsiDaily.OpsiDaily.BuySupply', 'OpsiShop.Scheduler.Enable'),
        # ('OpsiDaily.Scheduler.Enable', 'OpsiDaily.OpsiDaily.DoMission'),
        # ('OpsiShop.Scheduler.Enable', 'OpsiShop.OpsiShop.BuySupply'),
        # ('ShopOnce.GuildShop.Filter', 'ShopOnce.GuildShop.Filter', bp_redirect),
        # ('ShopOnce.MedalShop2.Filter', 'ShopOnce.MedalShop2.Filter', bp_redirect),
        # (('Alas.DropRecord.SaveResearch', 'Alas.DropRecord.UploadResearch'),
        #  'Alas.DropRecord.ResearchRecord', upload_redirect),
        # (('Alas.DropRecord.SaveCommission', 'Alas.DropRecord.UploadCommission'),
        #  'Alas.DropRecord.CommissionRecord', upload_redirect),
        # (('Alas.DropRecord.SaveOpsi', 'Alas.DropRecord.UploadOpsi'),
        #  'Alas.DropRecord.OpsiRecord', upload_redirect),
        # (('Alas.DropRecord.SaveMeowfficerTalent', 'Alas.DropRecord.UploadMeowfficerTalent'),
        #  'Alas.DropRecord.MeowfficerTalent', upload_redirect),
        # ('Alas.DropRecord.SaveCombat', 'Alas.DropRecord.CombatRecord', upload_redirect),
        # ('Alas.DropRecord.SaveMeowfficer', 'Alas.DropRecord.MeowfficerBuy', upload_redirect),
        # ('Alas.Emulator.PackageName', 'Alas.DropRecord.API', api_redirect),
        # ('Alas.RestartEmulator.Enable', 'Alas.RestartEmulator.ErrorRestart'),
        # ('OpsiGeneral.OpsiGeneral.BuyActionPoint', 'OpsiGeneral.OpsiGeneral.BuyActionPointLimit', action_point_redirect),
        # ('BattlePass.BattlePass.BattlePassReward', 'Freebies.BattlePass.Collect'),
        # ('DataKey.Scheduler.Enable', 'Freebies.DataKey.Collect'),
        # ('DataKey.DataKey.ForceGet', 'Freebies.DataKey.ForceCollect'),
        # ('SupplyPack.SupplyPack.WeeklyFreeSupplyPack', 'Freebies.SupplyPack.Collect'),
        # ('Commission.Commission.CommissionFilter', 'Commission.Commission.CustomFilter'),
        # 2023.02.17
        # ('OpsiAshBeacon.OpsiDossierBeacon.Enable', 'OpsiAshBeacon.OpsiAshBeacon.AttackMode', dossier_redirect),
        # ('General.Retirement.EnhanceFavourite', 'General.Enhance.ShipToEnhance', enhance_favourite_redirect),
        # ('General.Retirement.EnhanceFilter', 'General.Enhance.Filter'),
        # ('General.Retirement.EnhanceCheckPerCategory', 'General.Enhance.CheckPerCategory', enhance_check_redirect),
        # ('General.Retirement.OldRetireN', 'General.OldRetire.N'),
        # ('General.Retirement.OldRetireR', 'General.OldRetire.R'),
        # ('General.Retirement.OldRetireSR', 'General.OldRetire.SR'),
        # ('General.Retirement.OldRetireSSR', 'General.OldRetire.SSR'),
        # (('GemsFarming.GemsFarming.FlagshipChange', 'GemsFarming.GemsFarming.FlagshipEquipChange'),
        #  'GemsFarming.GemsFarming.ChangeFlagship',
        #  change_ship_redirect),
        # (('GemsFarming.GemsFarming.VanguardChange', 'GemsFarming.GemsFarming.VanguardEquipChange'),
        #  'GemsFarming.GemsFarming.ChangeVanguard',
        #  change_ship_redirect),
        # ('Alas.DropRecord.API', 'Alas.DropRecord.API', api_redirect2)
    ]
    # redirection += [
    #     (
    #         (f'{task}.Emotion.CalculateEmotion', f'{task}.Emotion.IgnoreLowEmotionWarn'),
    #         f'{task}.Emotion.Mode',
    #         emotion_mode_redirect
    #     ) for task in [
    #         'Main', 'Main2', 'Main3', 'GemsFarming',
    #         'Event', 'Event2', 'EventA', 'EventB', 'EventC', 'EventD', 'EventSp', 'Raid', 'RaidDaily',
    #         'Sos', 'WarArchives',
    #     ]
    # ]

    @cached_property
    def args(self):
        return read_file(filepath_args())

    def config_update(self, old, is_template=False):
        """
        Args:
            old (dict):
            is_template (bool):

        Returns:
            dict:
        """
        new = {}

        def deep_load(keys):
            data = deep_get(self.args, keys=keys, default={})
            value = deep_get(old, keys=keys, default=data['value'])
            typ = data['type']
            display = data.get('display')
            if is_template or value is None or value == '' \
                    or typ in ['lock', 'state'] or (display == 'hide' and typ != 'stored'):
                value = data['value']
            value = parse_value(value, data=data)
            deep_set(new, keys=keys, value=value)

        for path, _ in deep_iter(self.args, depth=3):
            deep_load(path)

        # AzurStatsID
        if is_template:
            deep_set(new, 'Alas.DropRecord.AzurStatsID', None)
        else:
            deep_default(new, 'Alas.DropRecord.AzurStatsID', random_id())
        # Update to latest event
        server = to_server(deep_get(new, 'Alas.Emulator.PackageName', 'cn'))
        if not is_template:
            for task in EVENTS + RAIDS + COALITIONS:
                deep_set(new,
                         keys=f'{task}.Campaign.Event',
                         value=deep_get(self.args, f'{task}.Campaign.Event.{server}'))
            for task in ['GemsFarming']:
                if deep_get(new, keys=f'{task}.Campaign.Event', default='campaign_main') != 'campaign_main':
                    deep_set(new,
                             keys=f'{task}.Campaign.Event',
                             value=deep_get(self.args, f'{task}.Campaign.Event.{server}'))
        # War archive does not allow campaign_main
        for task in WAR_ARCHIVES:
            if deep_get(new, keys=f'{task}.Campaign.Event', default='campaign_main') == 'campaign_main':
                deep_set(new,
                         keys=f'{task}.Campaign.Event',
                         value=deep_get(self.args, f'{task}.Campaign.Event.{server}'))

        # Events does not allow default stage 12-4
        def default_stage(t, stage):
            if deep_get(new, keys=f'{t}.Campaign.Name', default='12-4') in ['7-2', '12-4']:
                deep_set(new, keys=f'{t}.Campaign.Name', value=stage)

        for task in EVENTS + WAR_ARCHIVES:
            default_stage(task, 'D3')
        for task in COALITIONS:
            default_stage(task, 'TC-3')

        if not is_template:
            new = self.config_redirect(old, new)
        new = self._override(new)

        return new

    def config_redirect(self, old, new):
        """
        Convert old settings to the new.

        Args:
            old (dict):
            new (dict):

        Returns:
            dict:
        """
        for row in self.redirection:
            if len(row) == 2:
                source, target = row
                update_func = None
            elif len(row) == 3:
                source, target, update_func = row
            else:
                continue

            if isinstance(source, tuple):
                value = []
                error = False
                for attribute in source:
                    tmp = deep_get(old, keys=attribute)
                    if tmp is None:
                        error = True
                        continue
                    value.append(tmp)
                if error:
                    continue
            else:
                value = deep_get(old, keys=source)
                if value is None:
                    continue

            if update_func is not None:
                value = update_func(value)

            if isinstance(target, tuple):
                for k, v in zip(target, value):
                    # Allow update same key
                    if (deep_get(old, keys=k) is None) or (source == target):
                        deep_set(new, keys=k, value=v)
            elif (deep_get(old, keys=target) is None) or (source == target):
                deep_set(new, keys=target, value=value)

        return new

    def _override(self, data):
        def remove_drop_save(key):
            value = deep_get(data, keys=key, default='do_not')
            if value == 'save_and_upload':
                value = 'upload'
                deep_set(data, keys=key, value=value)
            elif value == 'save':
                value = 'do_not'
                deep_set(data, keys=key, value=value)

        if IS_ON_PHONE_CLOUD:
            deep_set(data, 'Alas.Emulator.Serial', '127.0.0.1:5555')
            deep_set(data, 'Alas.Emulator.ScreenshotMethod', 'DroidCast_raw')
            deep_set(data, 'Alas.Emulator.ControlMethod', 'MaaTouch')
            for arg in deep_get(self.args, keys='Alas.DropRecord', default={}).keys():
                remove_drop_save(arg)

        return data

    def save_callback(self, key: str, value: t.Any) -> t.Iterable[t.Tuple[str, t.Any]]:
        """
        Args:
            key: Key path in config json, such as "Main.Emotion.Fleet1Value"
            value: Value set by user, such as "98"

        Yields:
            str: Key path to set config json, such as "Main.Emotion.Fleet1Record"
            any: Value to set, such as "2020-01-01 00:00:00"
        """
        if "Emotion" in key and "Value" in key:
            key = key.split(".")
            key[-1] = key[-1].replace("Value", "Record")
            yield ".".join(key), datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Oh no, dynamic dropdown update can only be used on pywebio > 1.8.0
        # elif key == 'Alas.Emulator.ScreenshotMethod' and value == 'nemu_ipc':
        #     yield 'Alas.Emulator.ControlMethod', 'nemu_ipc'
        # elif key == 'Alas.Emulator.ControlMethod' and value == 'nemu_ipc':
        #     yield 'Alas.Emulator.ScreenshotMethod', 'nemu_ipc'

    def read_file(self, config_name, is_template=False):
        """
        Read and update config file.

        Args:
            config_name (str): ./config/{file}.json
            is_template (bool):

        Returns:
            dict:
        """
        old = read_file(filepath_config(config_name))
        new = self.config_update(old, is_template=is_template)
        # The updated config did not write into file, although it doesn't matters.
        # Commented for performance issue
        # self.write_file(config_name, new)
        return new

    @staticmethod
    def write_file(config_name, data, mod_name='alas'):
        """
        Write config file.

        Args:
            config_name (str): ./config/{file}.json
            data (dict):
            mod_name (str):
        """
        write_file(filepath_config(config_name, mod_name), data)

    @timer
    def update_file(self, config_name, is_template=False):
        """
        Read, update and write config file.

        Args:
            config_name (str): ./config/{file}.json
            is_template (bool):

        Returns:
            dict:
        """
        data = self.read_file(config_name, is_template=is_template)
        self.write_file(config_name, data)
        return data


if __name__ == '__main__':
    """
    Process the whole config generation.

                 task.yaml -+----------------> menu.json
             argument.yaml -+-> args.json ---> config_generated.py
             override.yaml -+       |
                  gui.yaml --------\|
                                   ||
    (old) i18n/<lang>.json --------\\========> i18n/<lang>.json
    (old)    template.json ---------\========> template.json
    """
    # Ensure running in Alas root folder
    import os

    os.chdir(os.path.join(os.path.dirname(__file__), '../../'))

    ConfigGenerator().generate()
    ConfigUpdater().update_file('template', is_template=True)