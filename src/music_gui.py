"""Windows desktop interface for the existing verified audio downloader."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from music_download import QUALITY_NAMES, audio_offers, download_selected, output_options
from qq_search import search_songs
from qq_playlist import fetch_playlist, PlaylistError
from download_queue import run_queue, POLICIES, FORMATS
from batch_export import safe_name


class MusicApp:
    def __init__(self, root):
        self.root = root
        self.busy = False
        self.events = queue.Queue()
        self.songs = []
        self.offers = {}
        self.offer_song = None
        self.quality_keys = []
        self.format_keys = []
        self.last_file = None
        self.queue_songs = []
        self.queue_folder = None
        self.cancel_event = threading.Event()
        self.operation = None
        root.title('QQ 音乐歌曲下载')
        root.geometry(f'{min(1180, root.winfo_screenwidth()-60)}x{min(850, root.winfo_screenheight()-100)}')
        root.minsize(900, 680)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.report_callback_exception = self.callback_error
        self.query = tk.StringVar()
        self.artist = tk.StringVar()
        self.use_account = tk.BooleanVar(value=False)
        self.playlist_url = tk.StringVar()
        self.batch_policy = tk.StringVar(value=POLICIES['best'])
        self.batch_format = tk.StringVar(value=FORMATS['original'])
        self.progress_text = tk.StringVar(value='尚未开始')
        self.result_text = tk.StringVar(value='搜索歌曲或导入公开歌单')
        self.output = tk.StringVar(value=str(Path.home() / 'Music' / 'QQmusic'))
        self.quality = tk.StringVar()
        self.output_format = tk.StringVar()
        self.status = tk.StringVar(value='输入歌名开始搜索。')
        self.selected_title = tk.StringVar(value='尚未选择歌曲')
        self._build()
        self._sync()
        self.poll_id = root.after(100, self._poll)

    def _build(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        self.root.configure(bg='#f3f5f8')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#f3f5f8')
        style.configure('TLabel', background='#f3f5f8', foreground='#25324a')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 16, 'bold'))
        style.configure('Hint.TLabel', foreground='#637087', font=('Microsoft YaHei UI', 9))
        style.configure('TButton', padding=(10, 4))
        style.configure('Accent.TButton', background='#187553', foreground='white')
        style.map('Accent.TButton', background=[('disabled', '#bdc7c2'), ('active', '#125a40')])
        line_height = tkfont.Font(family='Microsoft YaHei UI', size=10).metrics('linespace')
        style.configure('Treeview', rowheight=line_height+12, background='white', fieldbackground='white', borderwidth=0)
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=(8, 5), background='#e7edf3')
        style.map('Treeview', background=[('selected', '#dcefe7')], foreground=[('selected', '#153e31')])
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        table_minimum = (line_height+12)*4 + line_height*3+60
        frame.rowconfigure(4, weight=1, minsize=table_minimum)
        ttk.Label(frame, text='QQ 音乐歌曲下载', style='Title.TLabel').grid(row=0, sticky='w')
        sources = ttk.Notebook(frame)
        sources.grid(row=2, sticky='ew')
        search = ttk.Frame(sources, padding=(8, 8))
        sources.add(search, text='搜索歌曲')
        playlist = ttk.Frame(sources, padding=(8, 8))
        sources.add(playlist, text='导入歌单')
        playlist.columnconfigure(1, weight=1)
        ttk.Label(playlist, text='链接 / 歌单 ID').grid(row=0, column=0, padx=(0, 8))
        self.playlist_entry = ttk.Entry(playlist, textvariable=self.playlist_url)
        self.playlist_entry.grid(row=0, column=1, sticky='ew')
        self.import_button = ttk.Button(playlist, text='读取歌单', command=self.import_playlist)
        self.import_button.grid(row=0, column=2, padx=(8, 0))
        self.playlist_entry.bind('<Return>', lambda _: self.import_playlist())
        search.columnconfigure(1, weight=3)
        search.columnconfigure(3, weight=1)
        ttk.Label(search, text='歌名').grid(row=0, column=0, padx=(0, 8))
        self.query_entry = ttk.Entry(search, textvariable=self.query)
        self.query_entry.grid(row=0, column=1, sticky='ew')
        ttk.Label(search, text='歌手（可选）').grid(row=0, column=2, padx=10)
        self.artist_entry = ttk.Entry(search, textvariable=self.artist, width=17)
        self.artist_entry.grid(row=0, column=3, sticky='ew')
        self.search_button = ttk.Button(search, text='搜索歌曲', command=self.search, style='Accent.TButton')
        self.search_button.grid(row=0, column=4, padx=(12, 0))
        self.query_entry.bind('<Return>', lambda _: self.search())
        self.artist_entry.bind('<Return>', lambda _: self.search())
        self.query_entry.focus_set()
        toolbar = ttk.Frame(frame)
        toolbar.grid(row=3, sticky='ew', pady=(8, 4))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.result_text, style='Hint.TLabel').grid(row=0, column=0, sticky='w')
        self.all_button = ttk.Button(toolbar, text='全选', command=self.select_all)
        self.all_button.grid(row=0, column=1)
        self.none_button = ttk.Button(toolbar, text='清空选择', command=self.select_none)
        self.none_button.grid(row=0, column=2, padx=(6, 0))
        self.tables = ttk.Notebook(frame)
        self.tables.grid(row=4, sticky='nsew')
        table = ttk.Frame(self.tables)
        self.tables.add(table, text='歌曲列表')
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('check', 'title', 'artist', 'album', 'duration'),
                                 show='headings', selectmode='extended', height=8)
        for key, label, width in [('check', '选择', 56), ('title', '歌名 / 版本', 320), ('artist', '歌手', 170),
                                  ('album', '专辑', 230), ('duration', '时长', 76)]:
            self.tree.heading(key, text=label, anchor='center' if key in ('check', 'duration') else 'w')
            self.tree.column(key, width=width, minwidth=width if key in ('check', 'duration') else 120,
                             stretch=key not in ('check', 'duration'), anchor='center' if key in ('check', 'duration') else 'w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.tag_configure('odd', background='#f5f8fb')
        self.empty_label = ttk.Label(table, text='还没有歌曲\n输入歌名搜索，或切换到「导入歌单」', anchor='center', justify='center')
        self.empty_label.place(relx=0.5, rely=0.5, anchor='center')
        self.tree.bind('<<TreeviewSelect>>', self.selection_changed)
        self.tree.bind('<Double-1>', self.double_click)
        self.tree.bind('<Button-1>', self.table_click)
        self.tree.bind('<KeyPress>', lambda _: 'break' if self.busy else None)
        self.tree.bind('<Control-a>', self.select_all)
        queue_frame = ttk.Frame(self.tables)
        self.tables.add(queue_frame, text='下载队列 (0)')
        queue_frame.rowconfigure(0, weight=1)
        queue_frame.columnconfigure(0, weight=1)
        self.queue_tree = ttk.Treeview(queue_frame, columns=('title', 'artist', 'quality', 'status'), show='headings', selectmode='extended', height=8)
        for key, label, width in [('title', '歌曲', 350), ('artist', '歌手', 200), ('quality', '源音质', 130), ('status', '状态', 180)]:
            self.queue_tree.heading(key, text=label, anchor='w')
            self.queue_tree.column(key, width=width, minwidth=100)
        self.queue_tree.grid(row=0, column=0, sticky='nsew')
        qs = ttk.Scrollbar(queue_frame, command=self.queue_tree.yview)
        qs.grid(row=0, column=1, sticky='ns')
        qh = ttk.Scrollbar(queue_frame, orient='horizontal', command=self.queue_tree.xview)
        qh.grid(row=1, column=0, sticky='ew')
        self.queue_tree.configure(yscrollcommand=qs.set, xscrollcommand=qh.set)
        self.queue_tree.tag_configure('odd', background='#f5f8fb')
        queue_actions = ttk.Frame(queue_frame)
        queue_actions.grid(row=2, column=0, sticky='ew')
        self.remove_button = ttk.Button(queue_actions, text='移除选中', command=self.remove_queue)
        self.remove_button.pack(side='left')
        self.clear_queue_button = ttk.Button(queue_actions, text='清空队列', command=self.clear_queue)
        self.clear_queue_button.pack(side='left', padx=6)
        selected = ttk.Frame(frame)
        selected.grid(row=5, sticky='ew', pady=(6, 6))
        selected.columnconfigure(0, weight=1)
        ttk.Label(selected, textvariable=self.selected_title, width=40).grid(row=0, column=0, sticky='ew')
        self.detect_button = ttk.Button(selected, text='检测所选歌曲音质', command=self.detect)
        self.detect_button.grid(row=0, column=1, padx=(12, 0))
        self.add_button = ttk.Button(selected, text='加入下载队列', command=self.add_queue, style='Accent.TButton')
        self.add_button.grid(row=0, column=2, padx=(8, 0))
        self.settings = ttk.Notebook(frame)
        self.settings.grid(row=6, sticky='ew')
        options = ttk.Frame(self.settings, padding=8)
        self.settings.add(options, text='单曲设置')
        batch = ttk.Frame(self.settings, padding=8)
        self.settings.add(batch, text='批量设置')
        batch.columnconfigure(0, weight=1)
        batch.columnconfigure(1, weight=1)
        self.policy_box = ttk.Combobox(batch, textvariable=self.batch_policy, values=list(POLICIES.values()), state='readonly', width=26)
        self.policy_box.grid(row=0, column=0, sticky='ew')
        self.batch_format_box = ttk.Combobox(batch, textvariable=self.batch_format, values=list(FORMATS.values()), state='readonly', width=20)
        self.batch_format_box.grid(row=0, column=1, sticky='ew', padx=8)
        self.queue_button = ttk.Button(batch, text='下载队列', command=self.download_batch, style='Accent.TButton')
        self.queue_button.grid(row=0, column=2)
        self.stop_button = ttk.Button(batch, text='停止后续歌曲', command=self.stop_batch)
        self.stop_button.grid(row=0, column=3, padx=(8, 0))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)
        ttk.Label(options, text='源音质').grid(row=0, column=0, padx=(0, 8))
        self.quality_box = ttk.Combobox(options, textvariable=self.quality, state='readonly', width=23)
        self.quality_box.grid(row=0, column=1, sticky='ew')
        self.quality_box.bind('<<ComboboxSelected>>', self.quality_changed)
        ttk.Label(options, text='输出格式').grid(row=0, column=2, padx=(20, 8))
        self.format_box = ttk.Combobox(options, textvariable=self.output_format, state='readonly', width=29)
        self.format_box.grid(row=0, column=3, sticky='ew')
        folder = ttk.Frame(frame)
        folder.grid(row=8, sticky='ew', pady=(8, 0))
        folder.columnconfigure(1, weight=1)
        ttk.Label(folder, text='保存位置').grid(row=0, column=0, padx=(0, 8))
        self.path_entry = ttk.Entry(folder, textvariable=self.output)
        self.path_entry.grid(row=0, column=1, sticky='ew')
        self.browse_button = ttk.Button(folder, text='浏览…', command=self.browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))
        self.open_button = ttk.Button(folder, text='打开文件夹', command=self.open_folder)
        self.open_button.grid(row=0, column=3, padx=(8, 0))
        action = ttk.Frame(frame)
        action.grid(row=9, sticky='ew', pady=(6, 4))
        action.columnconfigure(0, weight=1)
        ttk.Label(action, textvariable=self.progress_text).grid(row=0, column=0, sticky='w')
        self.download_button = ttk.Button(action, text='开始下载', command=self.download, style='Accent.TButton')
        self.download_button.grid(row=0, column=1, padx=(12, 0))
        self.progress = ttk.Progressbar(frame, mode='determinate', maximum=100)
        self.progress.grid(row=10, sticky='ew', pady=(0, 8))
        self.log = tk.Text(frame, height=2, wrap='word', state='disabled', relief='flat',
                           bg='#e9edf2', fg='#4a5870', font=('Microsoft YaHei UI', 9), padx=10, pady=6)
        self.log.grid(row=11, sticky='ew')
        self.account_check = ttk.Checkbutton(frame, text='使用本机 QQ 音乐当前账号（读取登录会话，仅在内存使用）',
                                             variable=self.use_account, command=self.account_changed)
        self.account_check.grid(row=12, sticky='w', pady=(4, 0))
        self.root.update_idletasks()
        required = frame.winfo_reqheight() - self.tables.winfo_reqheight() + table_minimum
        minimum_width = max(900, *(widget.winfo_reqwidth()+32 for widget in (search, selected, options, batch, folder, action)))
        self.root.minsize(min(minimum_width, self.root.winfo_screenwidth()-80), required)
        scale = max(1, line_height / 19)
        self.root.geometry(f'{min(round(1180*scale), self.root.winfo_screenwidth()-80)}x{min(round(900*scale), self.root.winfo_screenheight()-100)}')

    def _message(self, text):
        self.status.set(text)
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 120:
            self.log.delete('1.0', '30.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def selected_song(self):
        selected = self.tree.selection()
        return self.songs[int(selected[0])] if len(selected) == 1 else None

    def selected_songs(self):
        chosen = set(self.tree.selection())
        return [song for i, song in enumerate(self.songs) if str(i) in chosen]

    def table_click(self, event):
        if self.busy:
            return 'break'
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == '#1':
            if row in self.tree.selection():
                self.tree.selection_remove(row)
            else:
                self.tree.selection_add(row)
            return 'break'

    def double_click(self, event):
        if self.tree.identify_column(event.x) != '#1':
            self.detect()
        return 'break'

    def select_all(self, _=None):
        if not self.busy:
            self.tree.selection_set(self.tree.get_children())
        return 'break'

    def select_none(self):
        if not self.busy:
            self.tree.selection_remove(self.tree.selection())

    def import_playlist(self):
        if self.busy:
            return
        value = self.playlist_url.get().strip()
        if not value:
            self._message('请粘贴歌单完整链接或输入数字 ID。')
            return
        self._start('playlist', lambda: fetch_playlist(value), '正在读取公开歌单……')

    def show_songs(self, songs):
        self._clear_offers()
        self.tree.delete(*self.tree.get_children())
        self.songs = songs
        for index, song in enumerate(songs):
            self.tree.insert('', 'end', iid=str(index), values=('□', song['songname'],
                ' / '.join(item['name'] for item in song['singer']), song['albumname'],
                f"{song['interval'] // 60}:{song['interval'] % 60:02d}"), tags=('odd',) if index % 2 else ())
        if songs:
            self.empty_label.place_forget()
        else:
            self.empty_label.configure(text='没有取得歌曲\n可缩短歌名、补充歌手，或检查歌单公开状态')
            self.empty_label.place(relx=0.5, rely=0.5, anchor='center')
        self.tables.select(0)
        self.selection_changed()

    def add_queue(self):
        if self.busy:
            return
        existing = {s['songid'] for s in self.queue_songs}
        added = 0
        for song in self.selected_songs():
            if song['songid'] not in existing:
                self.queue_songs.append(song)
                existing.add(song['songid'])
                self.queue_tree.insert('', 'end', iid=str(song['songid']), values=(song['songname'],
                    ' / '.join(s['name'] for s in song['singer']), '自动检测', '等待下载'),
                    tags=('odd',) if len(self.queue_songs) % 2 == 0 else ())
                added += 1
        self.tables.tab(1, text=f'下载队列 ({len(self.queue_songs)})')
        if self.queue_songs:
            self.tables.select(1)
            self.settings.select(1)
        self._message(f'加入 {added} 首，队列共 {len(self.queue_songs)} 首；重复歌曲自动忽略。')
        self._sync()

    def remove_queue(self):
        if self.busy:
            return
        ids = set(self.queue_tree.selection())
        self.queue_songs = [s for s in self.queue_songs if str(s['songid']) not in ids]
        for iid in ids:
            self.queue_tree.delete(iid)
        self.tables.tab(1, text=f'下载队列 ({len(self.queue_songs)})')
        self._sync()

    def clear_queue(self):
        if self.busy:
            return
        self.queue_tree.delete(*self.queue_tree.get_children())
        self.queue_songs = []
        self.tables.tab(1, text='下载队列 (0)')
        self._sync()

    def download_batch(self):
        if self.busy or not self.queue_songs:
            return
        raw = self.output.get().strip()
        folder = Path(raw).expanduser()
        if not raw or not folder.is_absolute():
            self._message('请选择保存文件夹，或输入完整的绝对路径。')
            return
        # Completed items stay visible but are not redownloaded on retry.
        songs = [s for s in self.queue_songs if self.queue_tree.set(str(s['songid']), 'status') != '已完成']
        if not songs:
            self._message('队列歌曲已经全部完成；可加入新歌曲，或清空后重新添加。')
            return
        policy = next(k for k, v in POLICIES.items() if v == self.batch_policy.get())
        fmt = next(k for k, v in FORMATS.items() if v == self.batch_format.get())
        self.cancel_event.clear()
        for song in songs:
            self.queue_tree.set(str(song['songid']), 'status', '等待下载')
        self.tables.select(1)
        use_account = self.use_account.get()
        self._start('batch', lambda: run_queue(songs, policy, fmt, folder, self.cancel_event,
                    lambda kind, value: self.events.put((kind, value)), use_account=use_account), f'准备处理 {len(songs)} 首歌曲……')
        self.progress_text.set(f'已处理 0 / {len(songs)} 首 · 0%')

    def stop_batch(self):
        if self.busy and self.operation == 'batch':
            self.cancel_event.set()
            self.stop_button.configure(state='disabled')
            self._message('已请求停止；正在下载的歌曲会完成保存与校验，随后停止。')

    def _sync(self):
        state = 'disabled' if self.busy else 'normal'
        for widget in (self.search_button, self.query_entry, self.artist_entry,
                       self.path_entry, self.browse_button, self.open_button, self.playlist_entry,
                       self.import_button, self.all_button, self.none_button, self.remove_button, self.clear_queue_button):
            widget.configure(state=state)
        self.detect_button.configure(state='normal' if not self.busy and self.selected_song() else 'disabled')
        ready = not self.busy and bool(self.offers) and self.selected_song() == self.offer_song
        self.quality_box.configure(state='readonly' if ready else 'disabled')
        self.format_box.configure(state='readonly' if ready else 'disabled')
        self.download_button.configure(state='normal' if ready else 'disabled')
        self.add_button.configure(state='normal' if not self.busy and self.selected_songs() else 'disabled')
        self.queue_button.configure(state='normal' if not self.busy and self.queue_songs else 'disabled')
        self.policy_box.configure(state='disabled' if self.busy else 'readonly')
        self.batch_format_box.configure(state='disabled' if self.busy else 'readonly')
        self.stop_button.configure(state='normal' if self.busy and self.operation == 'batch' and not self.cancel_event.is_set() else 'disabled')
        self.account_check.configure(state='disabled' if self.busy else 'normal')

    def account_changed(self):
        self._clear_offers()
        self._sync()
        self._message('账号访问方式已变更，请重新检测音质。')

    def _clear_offers(self):
        self.offers = {}
        self.offer_song = None
        self.quality_keys = []
        self.format_keys = []
        self.quality.set('')
        self.output_format.set('')
        self.quality_box.configure(values=())
        self.format_box.configure(values=())

    def selection_changed(self, _=None):
        if self.busy:
            return
        song = self.selected_song()
        if song != self.offer_song:
            self._clear_offers()
        count = len(self.tree.selection())
        self.selected_title.set(('已选：' + song['songname']) if song else (f'已选 {count} 首，可加入下载队列' if count else '尚未选择歌曲'))
        chosen = set(self.tree.selection())
        for iid in self.tree.get_children():
            self.tree.set(iid, 'check', '☑' if iid in chosen else '□')
        self.result_text.set(f'共 {len(self.songs)} 首 · 已选 {count} 首 · 勾选或 Ctrl / Shift 多选')
        self._sync()

    def _start(self, kind, work, status):
        self.busy = True
        self.operation = kind
        self._sync()
        self.progress.stop()
        self.progress.configure(mode='determinate' if kind == 'batch' else 'indeterminate', value=0)
        if kind != 'batch':
            self.progress.start(12)
        self.progress_text.set(status)
        self._message(status)

        def run():
            try:
                self.events.put((kind, work()))
            except PlaylistError as error:
                self.events.put(('user_error', str(error)))
            except Exception as error:
                # Exception text can contain signed URLs: show a category only.
                self.events.put(('error', type(error).__name__))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self._message(value)
                    continue
                if kind == 'queue_item':
                    iid = str(value['song_id'])
                    self.queue_tree.set(iid, 'status', value['status'])
                    if value.get('quality'):
                        self.queue_tree.set(iid, 'quality', QUALITY_NAMES[value['quality']])
                    self.queue_tree.see(iid)
                    continue
                if kind == 'queue_progress':
                    percent = value['processed'] / value['total'] * 100 if value['total'] else 0
                    self.progress.configure(value=percent)
                    self.progress_text.set(f"已处理 {value['processed']} / {value['total']} 首 · {percent:.0f}% · 成功 {value['success']} / 失败 {value['failed']} / 跳过 {value['skipped']}")
                    continue
                self.busy = False
                self.progress.stop()
                self.progress.configure(mode='determinate')
                if kind not in ('error', 'user_error', 'batch'):
                    self.progress.configure(value=100)
                    self.progress_text.set('操作完成 · 100%')
                if kind == 'search':
                    self.show_songs(value)
                    self._message(f'找到 {len(value)} 个版本。选择歌曲后点击检测音质，也可双击歌曲。' if value
                                  else '没有取得搜索结果。可缩短歌名、补充歌手，或检查网络后重试。')
                elif kind == 'playlist':
                    self.show_songs(value['songs'])
                    self.select_all()
                    # Keep an existing queue's destination stable when another playlist is opened.
                    if not self.queue_songs:
                        base = Path(self.output.get()).expanduser()
                        if self.queue_folder and base == self.queue_folder:
                            base = base.parent
                        self.queue_folder = base / (safe_name(value['name']) + '_' + value['id'])
                        self.output.set(str(self.queue_folder))
                    self.settings.select(1)
                    note = '；接口返回不完整或部分条目缺少资料，请核对数量' if value['partial'] else ''
                    self._message(f"已读取歌单「{value['name']}」：{len(value['songs'])} 首（标称 {value['total']} 首）{note}。已全选，可取消部分歌曲后加入队列。")
                elif kind == 'offers':
                    song, offers = value
                    if song == self.selected_song():
                        self.offer_song, self.offers = song, offers
                        self.quality_keys = [q for q in QUALITY_NAMES if q in offers]
                        self.quality_box.configure(values=[QUALITY_NAMES[q] for q in self.quality_keys])
                        if self.quality_keys:
                            self.quality_box.current(0)
                            self.quality_changed()
                    self._message(f'检测到 {len(offers)} 档音质。请选择音质、格式和保存位置，再点击开始下载。' if offers
                                  else '未取得可用音质。请检查网络、QQ 音乐登录状态与账号权限，或重新检测。')
                elif kind == 'download':
                    self.last_file = value
                    self.progress_text.set('下载完成并通过校验 · 100%')
                    self._message('下载完成并通过校验：' + str(value))
                elif kind == 'batch':
                    percent = value['processed'] / value['total'] * 100 if value['total'] else 0
                    self.progress.configure(value=percent)
                    ending = '已停止' if value['cancelled'] else '处理结束'
                    self.progress_text.set(f"{ending} · {value['processed']} / {value['total']} 首 · {percent:.0f}% · 成功 {value['success']} / 失败 {value['failed']} / 跳过 {value['skipped']}")
                    self._message(self.progress_text.get() + ('。日志未能完整写入。' if value.get('log_error') else '。日志：' + value['report']))
                elif kind == 'error':
                    if self.operation != 'batch':
                        self.progress.configure(value=0)
                    self.progress_text.set('操作失败，请查看下方说明')
                    self._message('操作失败（' + value + '）。请检查网络、账号权限和保存位置后重试。')
                    if self.operation == 'search':
                        self.empty_label.configure(text='搜索未完成，请检查网络后重试')
                elif kind == 'user_error':
                    self.progress.configure(value=0)
                    self.progress_text.set('未完成，请检查输入')
                    self._message(value)
                self.operation = None
                self._sync()
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self._poll)

    def search(self):
        if self.busy:
            return
        query, artist = self.query.get().strip(), self.artist.get().strip()
        if not query:
            self._message('请先输入歌名。')
            return
        self._clear_offers()
        self.tree.delete(*self.tree.get_children())
        self.songs = []
        self.empty_label.configure(text='正在搜索……')
        self.empty_label.place(relx=0.5, rely=0.5, anchor='center')
        self.selected_title.set('尚未选择歌曲')
        self._start('search', lambda: search_songs(query, artist=artist), '正在搜索官方歌曲信息……')

    def detect(self):
        if self.busy:
            return
        song = self.selected_song()
        if song is None:
            return
        self._clear_offers()
        use_account = self.use_account.get()
        def work():
            offers = audio_offers(song, progress=lambda message: self.events.put(('progress', message)), use_account=use_account)
            return song, offers
        self._start('offers', work, '正在检测所选歌曲的可用音质……')

    def quality_changed(self, _=None):
        index = self.quality_box.current()
        if index < 0 or index >= len(self.quality_keys):
            return
        options = output_options(self.quality_keys[index])
        self.format_keys = [key for key, _ in options]
        self.format_box.configure(values=[label for _, label in options])
        self.format_box.current(0)

    def browse(self):
        current = Path(self.output.get().strip()).expanduser()
        chosen = filedialog.askdirectory(parent=self.root, title='选择歌曲保存文件夹',
                                         initialdir=str(current) if current.is_dir() else str(Path.home()))
        if chosen:
            self.output.set(chosen)

    def open_folder(self):
        raw = self.output.get().strip()
        folder = Path(raw).expanduser()
        if not raw or not folder.is_dir():
            self._message('保存文件夹尚不存在，成功下载时会自动创建；也可以先点击浏览选择已有目录。')
            return
        try:
            os.startfile(str(folder.resolve()))
        except OSError:
            self._message('无法打开保存文件夹，请检查路径是否可访问。')

    def download(self):
        song = self.selected_song()
        if self.busy or not self.offers or song != self.offer_song:
            return
        raw = self.output.get().strip()
        folder = Path(raw).expanduser()
        if not raw or not folder.is_absolute():
            self._message('请选择保存文件夹，或输入完整的绝对路径。')
            return
        qi, fi = self.quality_box.current(), self.format_box.current()
        if qi < 0 or fi < 0:
            self._message('请先选择源音质和输出格式。')
            return
        quality, fmt = self.quality_keys[qi], self.format_keys[fi]
        session = self.offers[quality]
        def work():
            folder.mkdir(parents=True, exist_ok=True)
            # Fail before fetching audio if the selected destination is unwritable.
            with tempfile.TemporaryFile(dir=folder):
                pass
            return download_selected(song, quality, session, fmt, folder)
        self._start('download', work, '正在下载、转换并校验整首音频，请稍候……')

    def close(self):
        if self.busy:
            messagebox.showinfo('任务进行中', '请等待当前操作完成后再关闭，避免中断下载或校验。', parent=self.root)
            return
        self.offers.clear()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    def callback_error(self, exception, value, traceback):
        self._message('界面操作失败（' + exception.__name__ + '），请重试。')


def smoke_test(report):
    """Explicit offline packaging check; never reads a session or uses the network."""
    from batch_export import ROOT, run_ffmpeg
    from runtime_tools import find_tool
    result = {}
    root = tk.Tk()
    root.withdraw()
    try:
        app = MusicApp(root)
        root.update()
        result['window_created'] = True
        result['initial_download_disabled'] = str(app.download_button['state']) == 'disabled'
        result['account_access_default_off'] = not app.use_account.get()
        process = subprocess.run([find_tool('node'), '--version'], capture_output=True, timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        result['node_version'] = process.stdout.decode().strip()
        result['decrypt_script_present'] = (ROOT / 'qmc_decrypt.cjs').is_file()
        with tempfile.TemporaryDirectory(prefix='qqmusic-smoke-') as folder:
            target = Path(folder) / '测试音频.wav'
            run_ffmpeg(['-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.1', '-c:a', 'pcm_s16le', str(target)])
            result['audio_tool_works'] = target.stat().st_size > 1000
        result['ok'] = all(result[key] for key in ('window_created', 'initial_download_disabled',
                          'decrypt_script_present', 'audio_tool_works'))
    finally:
        app.close()
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', type=Path, help='Run offline packaged component checks and write a JSON report')
    args = parser.parse_args()
    if args.smoke_test:
        return smoke_test(args.smoke_test)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    MusicApp(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
