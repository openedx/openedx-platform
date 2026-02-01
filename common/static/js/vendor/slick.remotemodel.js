"use strict";
(() => {
  var __defProp = Object.defineProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: !0, configurable: !0, writable: !0, value }) : obj[key] = value;
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key != "symbol" ? key + "" : key, value);

  // src/slick.remotemodel.ts
  var SlickRemoteModel = class {
    constructor() {
      // private
      __publicField(this, "PAGESIZE", 50);
      __publicField(this, "data", { length: 0 });
      __publicField(this, "searchstr", "");
      __publicField(this, "sortcol", null);
      __publicField(this, "sortdir", 1);
      __publicField(this, "h_request");
      __publicField(this, "req", null);
      // ajax request
      // events
      __publicField(this, "onDataLoading", new Slick.Event("onDataLoading"));
      __publicField(this, "onDataLoaded", new Slick.Event("onDataLoaded"));
      if (!(window.$ || window.jQuery) || !window.$.jsonp)
        throw new Error("SlickRemoteModel requires both jQuery and jQuery jsonp library to be loaded.");
      this.init();
    }
    init() {
    }
    isDataLoaded(from, to) {
      for (let i = from; i <= to; i++)
        if (this.data[i] === void 0 || this.data[i] === null)
          return !1;
      return !0;
    }
    clear() {
      for (let key in this.data)
        delete this.data[key];
      this.data.length = 0;
    }
    ensureData(from, to) {
      if (this.req) {
        this.req.abort();
        for (let i = this.req.fromPage; i <= this.req.toPage; i++)
          this.data[i * this.PAGESIZE] = void 0;
      }
      from < 0 && (from = 0), this.data.length > 0 && (to = Math.min(to, this.data.length - 1));
      let fromPage = Math.floor(from / this.PAGESIZE), toPage = Math.floor(to / this.PAGESIZE);
      for (; this.data[fromPage * this.PAGESIZE] !== void 0 && fromPage < toPage; )
        fromPage++;
      for (; this.data[toPage * this.PAGESIZE] !== void 0 && fromPage < toPage; )
        toPage--;
      if (fromPage > toPage || fromPage === toPage && this.data[fromPage * this.PAGESIZE] !== void 0) {
        this.onDataLoaded.notify({ from, to });
        return;
      }
      let url = "http://octopart.com/api/v3/parts/search?apikey=68b25f31&include[]=short_description&show[]=uid&show[]=manufacturer&show[]=mpn&show[]=brand&show[]=octopart_url&show[]=short_description&q=" + this.searchstr + "&start=" + fromPage * this.PAGESIZE + "&limit=" + ((toPage - fromPage) * this.PAGESIZE + this.PAGESIZE);
      this.sortcol !== null && (url += "&sortby=" + this.sortcol + (this.sortdir > 0 ? "+asc" : "+desc")), this.h_request && window.clearTimeout(this.h_request), this.h_request = window.setTimeout(() => {
        for (let i = fromPage; i <= toPage; i++)
          this.data[i * this.PAGESIZE] = null;
        this.onDataLoading.notify({ from, to }), this.req = window.$.jsonp({
          url,
          callbackParameter: "callback",
          cache: !0,
          success: this.onSuccess,
          error: () => this.onError(fromPage, toPage)
        }), this.req.fromPage = fromPage, this.req.toPage = toPage;
      }, 50);
    }
    onError(fromPage, toPage) {
      alert("error loading pages " + fromPage + " to " + toPage);
    }
    onSuccess(resp) {
      let from = resp.request.start, to = from + resp.results.length;
      this.data.length = Math.min(parseInt(resp.hits), 1e3);
      for (let i = 0; i < resp.results.length; i++) {
        let item = resp.results[i].item;
        this.data[from + i] = item, this.data[from + i].index = from + i;
      }
      this.req = null, this.onDataLoaded.notify({ from, to });
    }
    reloadData(from, to) {
      for (let i = from; i <= to; i++)
        delete this.data[i];
      this.ensureData(from, to);
    }
    setSort(column, dir) {
      this.sortcol = column, this.sortdir = dir, this.clear();
    }
    setSearch(str) {
      this.searchstr = str, this.clear();
    }
  };
  window.Slick && (window.Slick.Data = window.Slick.Data || {}, window.Slick.Data.RemoteModel = SlickRemoteModel);
})();
//# sourceMappingURL=slick.remotemodel.js.map