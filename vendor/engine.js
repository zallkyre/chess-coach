/*
 * engine.js — a small UCI client for stockfish.js.
 *
 * The raw Worker gives you a message pipe. This gives you the four things a
 * chess UI actually needs to feel instant:
 *
 *   1. a serial search queue with cancellation (`stop`), so a stale search
 *      never leaks `info` lines into the next one;
 *   2. streamed evaluations — every `info depth ... score ... pv ...` line is
 *      handed to `onInfo` while the search is still running;
 *   3. a FEN-keyed LRU transposition cache, so a repeated position (or a
 *      transposition that reaches the same position) answers with zero search;
 *   4. an opening book built from the same line list the desktop app uses,
 *      indexed by FEN, so book moves are 0 ms.
 *
 * No build step, no imports. Load chess.js first, then this file:
 *
 *   <script src="vendor/chess.js"></script>
 *   <script src="vendor/engine.js"></script>
 *
 *   const engine = ChessEngine.create('vendor/stockfish.js', { hash: 32 });
 *   await engine.init();
 *   engine.search(fen, { depth: 14, onInfo: i => console.log(i.depth, i.cp) });
 */
(function (global) {
  'use strict';

  /* Same opening tree as app.py. The book is derived from it at first use, so
     there is no second data file to keep in sync. */
  var OPENINGS = [
    ['Ruy Lopez', 'e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O'],
    ['Italian Game', 'e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6 d3 d6'],
    ['Italian Game: Two Knights', 'e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Na5'],
    ['Scotch Game', 'e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6 Nc3 Bb4'],
    ['Four Knights Game', 'e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4'],
    ['Petrov Defence', 'e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4 d4 d5'],
    ['Vienna Game', 'e4 e5 Nc3 Nf6 f4 d5 fxe5 Nxe4'],
    ["King's Gambit", 'e4 e5 f4 exf4 Nf3 g5'],
    ["Bishop's Opening", 'e4 e5 Bc4 Nf6 d3 c6 Nf3 d5'],
    ['Sicilian Najdorf', 'e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6'],
    ['Sicilian Dragon', 'e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6'],
    ['Sicilian Closed', 'e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7'],
    ['Sicilian Alapin', 'e4 c5 c3 d5 exd5 Qxd5 d4 Nf6'],
    ['French Defence', 'e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7'],
    ['French Tarrasch', 'e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7'],
    ['French Exchange', 'e4 e6 d4 d5 exd5 exd5 Nf3 Nf6'],
    ['Caro-Kann', 'e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5'],
    ['Caro-Kann Advance', 'e4 c6 d4 d5 e5 c5'],
    ['Scandinavian Defence', 'e4 d5 exd5 Qxd5 Nc3 Qa5 d4 Nf6'],
    ['Scandinavian: Nf6', 'e4 d5 exd5 Nf6 d4 Nxd5 Nf3 g6'],
    ['Alekhine Defence', 'e4 Nf6 e5 Nd5 d4 d6 Nf3 dxe5 Nxe5 c6'],
    ['Modern Defence', 'e4 g6 d4 Bg7 Nc3 d6 f4 Nf6'],
    ['Pirc Defence', 'e4 d6 d4 Nf6 Nc3 g6 f4 Bg7'],
    ["Queen's Gambit Declined", 'd4 d5 c4 e6 Nc3 Nf6 Bg5 Be7 e3 O-O'],
    ["Queen's Gambit Slav", 'd4 d5 c4 c6 Nf3 Nf6 Nc3 e6 Bg5 dxc4'],
    ["Queen's Gambit Accepted", 'd4 d5 c4 dxc4 Nf3 Nf6 e3 e6 Bxc4 c5'],
    ["Queen's Gambit: Central", 'd4 d5 c4 e5 dxe5 d4 Nf3 Nc6'],
    ['London System', 'd4 d5 Bf4 Nf6 e3 e6 Nf3 c5'],
    ['Colle System', 'd4 d5 Nf3 Nf6 c4 e6'],
    ["King's Indian Defence", 'd4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3 O-O'],
    ['Nimzo-Indian Defence', 'd4 Nf6 c4 e6 Nc3 Bb4 e3 O-O'],
    ["Queen's Indian Defence", 'd4 Nf6 c4 e6 Nf3 b6 g3 Bb7 Bg2 Be7'],
    ['Benko Gambit', 'd4 Nf6 c4 c5 d5 b5'],
    ['Dutch Defence', 'd4 f5 g3 Nf6 Bg2 e6 Nf3 Be7'],
    ['English Opening', 'c4 e5 Nc3 Nf6 Nf3 Nc6 g3 d5'],
    ['English Symmetrical', 'c4 c5 Nf3 Nf6 d4 cxd4 Nxd4 e6'],
    ["King's Indian Attack", 'Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7'],
    ['Reti Opening', 'Nf3 Nf6 c4 e6 g3 d5 Bg2 Be7']
  ];

  /* ------------------------------------------------------------------ FEN */

  /* A position key that ignores the move counters and an en-passant square
     nobody can capture on. Two FENs that differ only in those are the same
     position, which is exactly what a transposition table must agree on. */
  function normFen(fen) {
    var s = String(fen || '').trim();
    var p = s.split(/\s+/);
    if (p.length < 4) return s;
    var ep = p[3];
    if (ep && ep !== '-' && typeof global.Chess === 'function') {
      try {
        var probe = new global.Chess([p[0], p[1], p[2], ep, '0', '1'].join(' '));
        var legal = probe.moves({ verbose: true });
        var usable = false;
        for (var i = 0; i < legal.length; i++) {
          if (legal[i].flags && legal[i].flags.indexOf('e') !== -1) { usable = true; break; }
        }
        if (!usable) ep = '-';
      } catch (e) { /* a malformed fen is compared as written */ }
    }
    return [p[0], p[1], p[2], ep].join(' ');
  }

  function hasPieceCount(fen, max) {
    var board = String(fen || '').split(' ')[0] || '';
    var n = 0;
    for (var i = 0; i < board.length; i++) {
      var c = board.charAt(i);
      if (c !== '/' && !(c >= '1' && c <= '8')) n++;   // piece letters only
    }
    return n <= max;
  }

  /* ------------------------------------------------------------------ LRU */
  function LRU(max) {
    this.max = max || 2048;
    this.map = new Map();
  }
  LRU.prototype.get = function (k) {
    if (!this.map.has(k)) return null;
    var v = this.map.get(k);
    this.map.delete(k);
    this.map.set(k, v);
    return v;
  };
  LRU.prototype.set = function (k, v) {
    if (this.map.has(k)) this.map.delete(k);
    this.map.set(k, v);
    while (this.map.size > this.max) {
      this.map.delete(this.map.keys().next().value);
    }
  };
  LRU.prototype.clear = function () { this.map.clear(); };
  Object.defineProperty(LRU.prototype, 'size', { get: function () { return this.map.size; } });

  /* --------------------------------------------------------------- parsing */
  function parseInfo(line) {
    var o = {}, m;
    m = /^info\s+depth\s+(\d+)/.exec(line); if (m) o.depth = +m[1];
    m = /\bseldepth\s+(\d+)/.exec(line); if (m) o.seldepth = +m[1];
    m = /\bmultipv\s+(\d+)/.exec(line); if (m) o.multipv = +m[1];
    m = /\bscore\s+(cp|mate)\s+(-?\d+)/.exec(line);
    if (m) {
      o.scoreType = m[1];
      o.score = +m[2];
      if (m[1] === 'cp') { o.cp = +m[2]; o.mate = null; }
      else { o.mate = +m[2]; o.cp = null; }
    }
    m = /\bnodes\s+(\d+)/.exec(line); if (m) o.nodes = +m[1];
    m = /\bnps\s+(\d+)/.exec(line); if (m) o.nps = +m[1];
    m = /\btime\s+(\d+)/.exec(line); if (m) o.time = +m[1];
    m = /\bpv\s+(.+)$/.exec(line); if (m) o.pv = m[1].trim().split(/\s+/);
    m = /\b(lowerbound|upperbound)\b/.exec(line); if (m) o.bound = m[1];
    return o;
  }

  /* -------------------------------------------------------------- book */
  var BOOK = null;
  function bookIndex() {
    if (BOOK) return BOOK;
    BOOK = new Map();
    if (typeof global.Chess !== 'function') return BOOK;
    for (var i = 0; i < OPENINGS.length; i++) {
      var name = OPENINGS[i][0];
      var line = OPENINGS[i][1].split(' ');
      var board;
      try { board = new global.Chess(); } catch (e) { return BOOK; }
      for (var j = 0; j < line.length; j++) {
        var key = normFen(board.fen());
        var mv;
        try { mv = board.move(line[j], { sloppy: true }); } catch (e) { mv = null; }
        if (!mv) break;
        if (!BOOK.has(key)) {
          BOOK.set(key, {
            uci: mv.from + mv.to + (mv.promotion || ''),
            san: mv.san,
            name: name,
            ply: j + 1
          });
        }
      }
    }
    return BOOK;
  }

  /* -------------------------------------------------------------- client */
  function Client(url, opts) {
    opts = opts || {};
    this.url = url;
    /* The bundled stockfish.js is a 32 MB fixed heap, so a 32 MB table cannot
       fit. 16 MB leaves room for the engine's own tables. */
    this.hash = Math.min(16, opts.hash || 16);
    this.multipv = opts.multipv || 1;
    this.cache = new LRU(opts.cacheSize || 2048);
    this.stats = { hits: 0, misses: 0, searches: 0 };
    this.ready = false;
    this._skill = 20;
    this._on = null;
    this._draining = false;
    this._drainTimer = null;
    this._goSent = false;
    this._stopRequested = false;
    this._abort = null;
    this._error = null;
    this._tail = Promise.resolve();
    this._pending = null;
    this.worker = new global.Worker(url);
    var self = this;
    this.worker.onmessage = function (e) { self._message(e); };
    this.worker.onerror = function (ev) {
      self._fail((ev && (ev.message || ev.type)) || 'worker error');
    };
    this.worker.onmessageerror = function () { self._fail('worker message error'); };
  }

  Client.prototype._fail = function (msg) {
    this._error = String(msg || 'engine error');
    this.ready = false;
    this._pending = null;     // so init() can actually retry a dead worker
    this._draining = false;
    this._goSent = false;
    if (this._drainTimer) { clearTimeout(this._drainTimer); this._drainTimer = null; }
    this._on = null;
    var abort = this._abort;
    this._abort = null;
    if (abort) abort();
  };

  Client.prototype._beginDrain = function () {
    var self = this;
    this._draining = true;
    if (this._drainTimer) clearTimeout(this._drainTimer);
    /* A trailing bestmove that never comes must not swallow the next search. */
    this._drainTimer = setTimeout(function () {
      self._draining = false;
      self._drainTimer = null;
    }, 4000);
  };

  Client.prototype._message = function (e) {
    var d = e && e.data;
    var line = typeof d === 'string' ? d : (d && typeof d.data === 'string' ? d.data : '');
    if (!line) return;
    if (this._draining) {
      if (line.indexOf('bestmove') === 0) {
        this._draining = false;
        if (this._drainTimer) { clearTimeout(this._drainTimer); this._drainTimer = null; }
      }
      return;
    }
    /* A bestmove with no active `go` is left over from a cancelled search.
       Dropping it here keeps it from ending the next search with the wrong move. */
    if (line.indexOf('bestmove') === 0 && !this._goSent) return;
    if (this._on) this._on(line);
  };

  Client.prototype._send = function (cmd) { this.worker.postMessage(cmd); };

  Client.prototype._queue = function (fn) {
    var run = this._tail.then(fn, fn);
    this._tail = run.then(function () {}, function () {});
    return run;
  };

  /* Resolves with the next line matching pred, or null on timeout. */
  Client.prototype._wait = function (pred, ms) {
    var self = this;
    return new Promise(function (resolve) {
      var t = setTimeout(function () { self._on = null; resolve(null); }, ms);
      self._on = function (line) {
        if (!pred(line)) return;
        clearTimeout(t);
        self._on = null;
        resolve(line);
      };
    });
  };

  Client.prototype.init = function () {
    if (this._pending) return this._pending;
    var self = this;
    this._pending = this._queue(async function () {
      self._send('uci');
      var uciok = await self._wait(function (l) { return l === 'uciok'; }, 8000);
      if (!uciok) { self._fail('no uciok'); return self; }
      self._send('setoption name Hash value ' + self.hash);
      self._send('setoption name Ponder value false');
      self._send('setoption name MultiPV value ' + self.multipv);
      self._send('isready');
      /* a timeout is not "ready": leaving ready=true here made every later
         search talk to an engine that never finished booting */
      var readyok = await self._wait(function (l) { return l === 'readyok'; }, 8000);
      if (!readyok) { self._fail('no readyok'); return self; }
      self.ready = true;
      self.setSkill(self._skill);
      return self;
    });
    return this._pending;
  };

  Client.prototype.setSkill = function (n) {
    this._skill = Math.max(0, Math.min(20, n | 0));
    /* Before `uciok` this command would be dropped on the floor; init()
       re-applies the stored value once the engine is ready. */
    if (this.ready) this._send('setoption name Skill Level value ' + this._skill);
  };

  /* Ask the current search to end now. It still resolves with its best so far. */
  Client.prototype.stop = function () {
    if (this._on) {
      this._send('stop');
    } else {
      /* Queued but not started: remember it so _run() stops as soon as it can. */
      this._stopRequested = true;
    }
  };

  Client.prototype.book = function (fen) {
    var hit = bookIndex().get(normFen(fen));
    return hit ? Object.assign({}, hit) : null;
  };

  Client.prototype.lookup = function (fen) { return this.cache.get(normFen(fen)); };

  Client.prototype.clear = function () {
    var self = this;
    this.cache.clear();
    this._stopRequested = false;
    /* The readyok has to be consumed here, or it lands on whatever search
       starts next and is mistaken for that search's output. */
    return this._queue(async function () {
      self._send('ucinewgame');
      self._send('isready');
      await self._wait(function (l) { return l === 'readyok'; }, 5000);
    });
  };

  /*
   * One bounded search.
   *
   *   depth        fixed depth (0 = use movetime)
   *   movetime     milliseconds of search when depth is 0
   *   skill        Stockfish Skill Level for this search (default 20)
   *   multiPV      number of pv lines (needs a fresh search, not cached)
   *   searchmoves  restrict the root to these UCI moves
   *   onInfo       called with every completed depth while thinking
   *   fresh        skip the transposition cache
   *
   * Resolves with { cp, mate, best, pv, depth, nodes, nps, cached, ok }.
   */
  Client.prototype.search = function (fen, opts) {
    opts = opts || {};
    var self = this;
    var depth = opts.depth | 0;
    var movetime = opts.movetime | 0;
    var skill = opts.skill === undefined ? 20 : (opts.skill | 0);
    var pvLines = opts.multiPV || 1;
    var key = normFen(fen) + (skill < 20 ? '|s' + skill : '') +
      (opts.searchmoves ? '|m' + opts.searchmoves : '') +
      (pvLines > 1 ? '|pv' + pvLines : '');

    /* Look the position up inside the queue, not before it: a search queued
       behind an identical one should reuse that result instead of repeating it. */
    return this._queue(function () {
      if (!opts.fresh && !opts.searchmoves && (opts.multiPV || 1) === 1) {
        var hit = self.cache.get(key);
        var minDepth = opts.minCachedDepth || 8;
        if (hit && (depth ? hit.depth >= depth : hit.depth >= minDepth)) {
          self.stats.hits++;
          var cached = Object.assign({}, hit, {
            cached: true, fen: fen, best: hit.best,
            pv: (hit.pv || []).slice()   // never hand out the cache's own array
          });
          if (opts.onInfo) opts.onInfo(cached);
          return cached;
        }
      }
      self.stats.misses++;
      return self._run(fen, key, opts);
    });
  };

  Client.prototype._run = function (fen, key, opts) {
    var self = this;
    return new Promise(function (resolve) {
      var state = {
        depth: 0, seldepth: 0, cp: 0, mate: null,
        nodes: 0, nps: 0, pv: [], best: null
      };
      var done = false;
      var started = Date.now();
      var lastEmit = 0;
      var throttle = opts.throttleMs === undefined ? 40 : opts.throttleMs;
      var hard = opts.timeout || (opts.depth ? 10000 : (opts.movetime || 300) + 5000);
      var forced = false;
      var guard, safety;

      function finish(reason) {
        if (done) return;
        done = true;
        clearTimeout(guard);
        clearTimeout(safety);
        self._on = null;
        self._abort = null;
        self._goSent = false;
        if ((opts.multiPV || 1) > 1) self._send('setoption name MultiPV value 1');
        var res = {
          cp: state.cp, mate: state.mate, best: state.best,
          pv: state.pv.slice(), depth: state.depth, seldepth: state.seldepth,
          nodes: state.nodes, nps: state.nps,
          ms: Date.now() - started, cached: false, ok: !!state.best,
          reason: reason || 'bestmove', fen: fen
        };
        /* Only a straight single-PV search may seed the transposition cache:
           a MultiPV line 1 is scored under a different root widening. */
        if (state.best && state.depth > 0 && !opts.noCache && (opts.multiPV || 1) === 1) {
          self.cache.set(key, res);
        }
        self.stats.searches++;
        resolve(res);
      }

      self._abort = function () { finish('error'); };

      self._on = function (line) {
        if (line.indexOf('info') === 0) {
          if (/\bcurrmove\b/.test(line)) return;
          var p = parseInfo(line);
          if (p.multipv && p.multipv > 1) return;   // pv 1 only drives the UI
          /* Fail-high / fail-low lines carry a tentative score and a one-move
             PV. Using them would show a fake eval and a truncated best line. */
          if (p.bound) return;
          if (p.depth) state.depth = p.depth;
          if (p.seldepth) state.seldepth = p.seldepth;
          if (p.cp !== undefined) state.cp = p.cp;
          if (p.mate !== undefined) state.mate = p.mate;
          if (p.nodes) state.nodes = p.nodes;
          if (p.nps) state.nps = p.nps;
          if (p.pv) state.pv = p.pv;
          var now = Date.now();
          var scored = p.cp !== undefined || p.mate !== undefined;
          if (opts.onInfo && scored && now - lastEmit >= throttle) {
            lastEmit = now;
            opts.onInfo({
              cp: state.cp, mate: state.mate, depth: state.depth,
              seldepth: state.seldepth, nodes: state.nodes, nps: state.nps,
              pv: state.pv.slice(), best: state.pv[0] || null,
              cached: false, fen: fen
            });
          }
          return;
        }
        if (line.indexOf('bestmove') === 0) {
          var tok = line.split(/\s+/)[1];
          if (tok && tok !== '(none)') state.best = tok;
          finish(forced ? 'stopped' : 'bestmove');
        }
      };

      self.setSkill(opts.skill === undefined ? 20 : opts.skill);
      if ((opts.multiPV || 1) > 1) {
        self._send('setoption name MultiPV value ' + opts.multiPV);
      }
      self._send('position fen ' + fen);
      var go = opts.depth ? ('go depth ' + opts.depth)
                          : ('go movetime ' + (opts.movetime || 300));
      if (opts.searchmoves) go += ' searchmoves ' + opts.searchmoves;
      self._goSent = true;
      self._send(go);

      /* stop() called while this job was still queued: honour it now. */
      if (self._stopRequested) {
        self._stopRequested = false;
        self._send('stop');
      }

      /* Ask politely at the deadline (bestmove arrives almost at once), then
         give up on the line entirely if the engine never answers. */
      guard = setTimeout(function () {
        forced = true;
        self._send('stop');
        safety = setTimeout(function () {
          self._beginDrain();   // swallow that search's trailing bestmove
          finish('timeout');
        }, 1500);
      }, hard);
    });
  };

  /* A move to play, at a strength. Uses the cache when the position is known. */
  Client.prototype.play = function (fen, opts) {
    opts = opts || {};
    return this.search(fen, {
      movetime: Math.max(60, opts.movetime || 200),
      skill: opts.skill === undefined ? 20 : opts.skill,
      onInfo: opts.onInfo
    });
  };

  /* A hint: best move plus score, cached under the full-strength key. */
  Client.prototype.best = function (fen, opts) {
    opts = opts || {};
    return this.search(fen, {
      movetime: Math.max(100, opts.movetime || 300),
      skill: 20,
      onInfo: opts.onInfo
    });
  };

  global.ChessEngine = {
    create: function (url, opts) { return new Client(url || 'vendor/stockfish.js', opts); },
    normFen: normFen,
    hasPieceCount: hasPieceCount,
    openings: OPENINGS
  };
})(typeof window !== 'undefined' ? window : this);
