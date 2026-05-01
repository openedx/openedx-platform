/*
 * decaffeinate suggestions:
 * DS101: Remove unnecessary use of Array.from
 * DS207: Consider shorter variations of null checks
 * Full docs: https://github.com/decaffeinate/decaffeinate/blob/master/docs/suggestions.md
 */
describe("Problem", function () {
  const problem_content_default = readFixtures("problem_content.html");

  beforeEach(function () {
    // Stub MathJax
    window.MathJax = {
      Hub: jasmine.createSpyObj("MathJax.Hub", ["getAllJax", "Queue"]),
      Callback: jasmine.createSpyObj("MathJax.Callback", ["After"]),
    };
    this.stubbedJax = { root: jasmine.createSpyObj("jax.root", ["toMathML"]) };
    MathJax.Hub.getAllJax.and.returnValue([this.stubbedJax]);
    window.update_schematics = function () {};
    spyOn(SR, "readText");
    spyOn(SR, "readTexts");

    // Load this function from spec/helper.js
    // Note that if your test fails with a message like:
    // 'External request attempted for blah, which is not defined.'
    // this msg is coming from the stubRequests function else clause.
    jasmine.stubRequests();

    loadFixtures("problem.html");

    spyOn(Logger, "log");
    spyOn($.fn, "load").and.callFake(function (url, callback) {
      $(this).html(readFixtures("problem_content.html"));
      return callback();
    });
  });

  describe("constructor", function () {
    it("set the element from html", function () {
      this.problem999 = new Problem(`\
<section class='xblock xblock-student_view xmodule_display xmodule_CapaModule' data-type='Problem'> \
<section id='problem_999' \
class='problems-wrapper' \
data-problem-id='i4x://edX/999/problem/Quiz' \
data-url='/problem/quiz/'> \
</section> \
</section>\
`);
      expect(this.problem999.element_id).toBe("problem_999");
    });

    it("set the element from loadFixtures", function () {
      this.problem1 = new Problem($(".xblock-student_view"));
      expect(this.problem1.element_id).toBe("problem_1");
    });
  });

  describe("bind", function () {
    beforeEach(function () {
      spyOn(window, "update_schematics");
      MathJax.Hub.getAllJax.and.returnValue([this.stubbedJax]);
      this.problem = new Problem($(".xblock-student_view"));
    });

    it("set mathjax typeset", () => expect(MathJax.Hub.Queue).toHaveBeenCalled());

    it("update schematics", () => expect(window.update_schematics).toHaveBeenCalled());

    it("bind answer refresh on button click", function () {
      expect($("div.action button")).toHandleWith("click", this.problem.refreshAnswers);
    });

    it("bind the submit button", function () {
      expect($(".action .submit")).toHandleWith("click", this.problem.submit_fd);
    });

    it("bind the reset button", function () {
      expect($("div.action button.reset")).toHandleWith("click", this.problem.reset);
    });

    it("bind the show button", function () {
      expect($(".action .show")).toHandleWith("click", this.problem.show);
    });

    it("bind the save button", function () {
      expect($("div.action button.save")).toHandleWith("click", this.problem.save);
    });

    it("bind the math input", function () {
      expect($("input.math")).toHandleWith("keyup", this.problem.refreshMath);
    });
  });

  describe("bind_with_custom_input_id", function () {
    beforeEach(function () {
      spyOn(window, "update_schematics");
      MathJax.Hub.getAllJax.and.returnValue([this.stubbedJax]);
      this.problem = new Problem($(".xblock-student_view"));
      return $(this).html(readFixtures("problem_content_1240.html"));
    });

    it("bind the submit button", function () {
      expect($(".action .submit")).toHandleWith("click", this.problem.submit_fd);
    });

    it("bind the show button", function () {
      expect($("div.action button.show")).toHandleWith("click", this.problem.show);
    });
  });

  describe("renderProgressState", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
    });

    const testProgessData = function (
      problem,
      score,
      total_possible,
      attempts,
      graded,
      expected_progress_after_render,
    ) {
      problem.el.data("problem-score", score);
      problem.el.data("problem-total-possible", total_possible);
      problem.el.data("attempts-used", attempts);
      problem.el.data("graded", graded);
      expect(problem.$(".problem-progress").html()).toEqual("");
      problem.renderProgressState();
      expect(problem.$(".problem-progress").html()).toEqual(expected_progress_after_render);
    };

    describe('with a status of "none"', function () {
      it("reports the number of points possible and graded", function () {
        testProgessData(this.problem, 0, 1, 0, "True", "1 point possible (graded)");
      });

      it("displays the number of points possible when rendering happens with the content", function () {
        testProgessData(this.problem, 0, 2, 0, "True", "2 points possible (graded)");
      });

      it("reports the number of points possible and ungraded", function () {
        testProgessData(this.problem, 0, 1, 0, "False", "1 point possible (ungraded)");
      });

      it("displays ungraded if number of points possible is 0", function () {
        testProgessData(this.problem, 0, 0, 0, "False", "0 points possible (ungraded)");
      });

      it("displays ungraded if number of points possible is 0, even if graded value is True", function () {
        testProgessData(this.problem, 0, 0, 0, "True", "0 points possible (ungraded)");
      });

      it("reports the correct score with status none and >0 attempts", function () {
        testProgessData(this.problem, 0, 1, 1, "True", "0/1 point (graded)");
      });

      it("reports the correct score with >1 weight, status none, and >0 attempts", function () {
        testProgessData(this.problem, 0, 2, 2, "True", "0/2 points (graded)");
      });
    });

    describe("with any other valid status", function () {
      it("reports the current score", function () {
        testProgessData(this.problem, 1, 1, 1, "True", "1/1 point (graded)");
      });

      it("shows current score when rendering happens with the content", function () {
        testProgessData(this.problem, 2, 2, 1, "True", "2/2 points (graded)");
      });

      it("reports the current score even if problem is ungraded", function () {
        testProgessData(this.problem, 1, 1, 1, "False", "1/1 point (ungraded)");
      });
    });

    describe('with valid status and string containing an integer like "0" for detail', () =>
      // These tests are to address a failure specific to Chrome 51 and 52 +
      it("shows 0 points possible for the detail", function () {
        testProgessData(this.problem, 0, 0, 1, "False", "0 points possible (ungraded)");
      }));

    describe("with a score of null (show_correctness == false)", function () {
      it("reports the number of points possible and graded, results hidden", function () {
        testProgessData(this.problem, null, 1, 0, "True", "1 point possible (graded, results hidden)");
      });

      it("reports the number of points possible (plural) and graded, results hidden", function () {
        testProgessData(this.problem, null, 2, 0, "True", "2 points possible (graded, results hidden)");
      });

      it("reports the number of points possible and ungraded, results hidden", function () {
        testProgessData(this.problem, null, 1, 0, "False", "1 point possible (ungraded, results hidden)");
      });

      it("displays ungraded if number of points possible is 0, results hidden", function () {
        testProgessData(this.problem, null, 0, 0, "False", "0 points possible (ungraded, results hidden)");
      });

      it("displays ungraded if number of points possible is 0, even if graded value is True, results hidden", function () {
        testProgessData(this.problem, null, 0, 0, "True", "0 points possible (ungraded, results hidden)");
      });

      it("reports the correct score with status none and >0 attempts, results hidden", function () {
        testProgessData(this.problem, null, 1, 1, "True", "1 point possible (graded, results hidden)");
      });

      it("reports the correct score with >1 weight, status none, and >0 attempts, results hidden", function () {
        testProgessData(this.problem, null, 2, 2, "True", "2 points possible (graded, results hidden)");
      });
    });
  });

  describe("render", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.bind = this.problem.bind;
      spyOn(this.problem, "bind");
    });

    describe("with content given", function () {
      beforeEach(function () {
        this.problem.render("Hello World");
      });

      it("render the content", function () {
        expect(this.problem.el.html()).toEqual("Hello World");
      });

      it("re-bind the content", function () {
        expect(this.problem.bind).toHaveBeenCalled();
      });
    });

    describe("with no content given", function () {
      beforeEach(function () {
        spyOn($, "postWithPrefix").and.callFake((url, callback) => callback({ html: "Hello World" }));
        this.problem.render();
      });

      it("load the content via ajax", function () {
        expect(this.problem.el.html()).toEqual("Hello World");
      });

      it("re-bind the content", function () {
        expect(this.problem.bind).toHaveBeenCalled();
      });
    });
  });

  describe("submit_fd", function () {
    beforeEach(function () {
      // Insert an input of type file outside of the problem.
      $(".xblock-student_view").after('<input type="file" />');
      this.problem = new Problem($(".xblock-student_view"));
      spyOn(this.problem, "submit");
    });

    it("submit method is called if input of type file is not in problem", function () {
      this.problem.submit_fd();
      expect(this.problem.submit).toHaveBeenCalled();
    });
  });

  describe("submit", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.answers = "foo=1&bar=2";
    });

    it("log the problem_check event", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
          done(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.submit();
      expect(Logger.log).toHaveBeenCalledWith("problem_check", "foo=1&bar=2");
    });

    it("log the problem_graded event, after the problem is done grading.", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        const response = {
          success: "correct",
          contents: "mock grader response",
        };
        callback(response);
        promise = {
          always(callable) {
            return callable();
          },
          done(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.submit();
      expect(Logger.log).toHaveBeenCalledWith(
        "problem_graded",
        ["foo=1&bar=2", "mock grader response"],
        this.problem.id,
      );
    });

    it("submit the answer for submit", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
          done(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.submit();
      expect($.postWithPrefix).toHaveBeenCalledWith(
        "/problem/Problem1/problem_check",
        "foo=1&bar=2",
        jasmine.any(Function),
      );
    });

    describe("when the response is correct", () =>
      it("call render with returned content", function () {
        const contents =
          '<div class="wrapper-problem-response" aria-label="Question 1"><p>Correct<span class="status">excellent</span></p></div>' +
          '<div class="wrapper-problem-response" aria-label="Question 2"><p>Yep<span class="status">correct</span></p></div>';
        spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
          let promise;
          callback({ success: "correct", contents });
          promise = {
            always(callable) {
              return callable();
            },
            done(callable) {
              return callable();
            },
          };
          return promise;
        });
        this.problem.submit();
        expect(this.problem.el).toHaveHtml(contents);
        expect(window.SR.readTexts).toHaveBeenCalledWith(["Question 1: excellent", "Question 2: correct"]);
      }));

    describe("when the response is incorrect", () =>
      it("call render with returned content", function () {
        const contents = '<p>Incorrect<span class="status">no, try again</span></p>';
        spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
          let promise;
          callback({ success: "incorrect", contents });
          promise = {
            always(callable) {
              return callable();
            },
            done(callable) {
              return callable();
            },
          };
          return promise;
        });
        this.problem.submit();
        expect(this.problem.el).toHaveHtml(contents);
        expect(window.SR.readTexts).toHaveBeenCalledWith(["no, try again"]);
      }));

    it("tests if the submit button is disabled while submitting and the text changes on the button", function () {
      const self = this;
      const curr_html = this.problem.el.html();
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        // At this point enableButtons should have been called, making the submit button disabled with text 'submitting'
        let promise;
        expect(self.problem.submitButton).toHaveAttr("disabled");
        expect(self.problem.submitButtonLabel.text()).toBe("Submitting");
        callback({
          success: "incorrect", // does not matter if correct or incorrect here
          contents: curr_html,
        });
        promise = {
          always(callable) {
            return callable();
          },
          done(callable) {
            return callable();
          },
        };
        return promise;
      });
      // Make sure the submit button is enabled before submitting
      $("#input_example_1").val("test").trigger("input");
      expect(this.problem.submitButton).not.toHaveAttr("disabled");
      this.problem.submit();
      // After submit, the button should not be disabled and should have text as 'Submit'
      expect(this.problem.submitButtonLabel.text()).toBe("Submit");
      expect(this.problem.submitButton).not.toHaveAttr("disabled");
    });
  });

  describe("submit button on problems", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.submitDisabled = (disabled) => {
        if (disabled) {
          expect(this.problem.submitButton).toHaveAttr("disabled");
        } else {
          expect(this.problem.submitButton).not.toHaveAttr("disabled");
        }
      };
    });

    describe("some basic tests for submit button", () =>
      it("should become enabled after a value is entered into the text box", function () {
        $("#input_example_1").val("test").trigger("input");
        this.submitDisabled(false);
        $("#input_example_1").val("").trigger("input");
        this.submitDisabled(true);
      }));

    describe("some advanced tests for submit button", function () {
      const radioButtonProblemHtml = readFixtures("radiobutton_problem.html");
      const checkboxProblemHtml = readFixtures("checkbox_problem.html");

      it("should become enabled after a checkbox is checked", function () {
        $("#input_example_1").replaceWith(checkboxProblemHtml);
        this.problem.submitAnswersAndSubmitButton(true);
        this.submitDisabled(true);
        $("#input_1_1_1").click();
        this.submitDisabled(false);
        $("#input_1_1_1").click();
        this.submitDisabled(true);
      });

      it("should become enabled after a radiobutton is checked", function () {
        $("#input_example_1").replaceWith(radioButtonProblemHtml);
        this.problem.submitAnswersAndSubmitButton(true);
        this.submitDisabled(true);
        $("#input_1_1_1").attr("checked", true).trigger("click");
        this.submitDisabled(false);
        $("#input_1_1_1").attr("checked", false).trigger("click");
        this.submitDisabled(true);
      });

      it("should become enabled after a value is selected in a selector", function () {
        const html = `\
<div id="problem_sel">
<select>
<option value="val0">Select an option</option>
<option value="val1">1</option>
<option value="val2">2</option>
</select>
</div>\
`;
        $("#input_example_1").replaceWith(html);
        this.problem.submitAnswersAndSubmitButton(true);
        this.submitDisabled(true);
        $("#problem_sel select").val("val2").trigger("change");
        this.submitDisabled(false);
        $("#problem_sel select").val("val0").trigger("change");
        this.submitDisabled(true);
      });

      it("should become enabled after a radiobutton is checked and a value is entered into the text box", function () {
        $(radioButtonProblemHtml).insertAfter("#input_example_1");
        this.problem.submitAnswersAndSubmitButton(true);
        this.submitDisabled(true);
        $("#input_1_1_1").attr("checked", true).trigger("click");
        this.submitDisabled(true);
        $("#input_example_1").val("111").trigger("input");
        this.submitDisabled(false);
        $("#input_1_1_1").attr("checked", false).trigger("click");
        this.submitDisabled(true);
      });

      it("should become enabled if there are only hidden input fields", function () {
        const html = `\
<input type="text" name="test" id="test" aria-describedby="answer_test" value="" style="display:none;">\
`;
        $("#input_example_1").replaceWith(html);
        this.problem.submitAnswersAndSubmitButton(true);
        this.submitDisabled(false);
      });
    });
  });

  describe("reset", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
    });

    it("log the problem_reset event", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.answers = "foo=1&bar=2";
      this.problem.reset();
      expect(Logger.log).toHaveBeenCalledWith("problem_reset", "foo=1&bar=2");
    });

    it("POST to the problem reset page", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.reset();
      expect($.postWithPrefix).toHaveBeenCalledWith(
        "/problem/Problem1/problem_reset",
        { id: "i4x://edX/101/problem/Problem1" },
        jasmine.any(Function),
      );
    });

    it("render the returned content", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        callback({ html: "Reset", success: true });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.reset();
      expect(this.problem.el.html()).toEqual("Reset");
    });

    it("sends a message to the window SR element", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        callback({ html: "Reset", success: true });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.reset();
      expect(window.SR.readText).toHaveBeenCalledWith("This problem has been reset.");
    });

    it("shows a notification on error", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        callback({ msg: "Error on reset.", success: false });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.reset();
      expect($(".notification-gentle-alert .notification-message").text()).toEqual("Error on reset.");
    });

    it("tests that reset does not enable submit or modify the text while resetting", function () {
      const self = this;
      const curr_html = this.problem.el.html();
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        // enableButtons should have been called at this point to set them to all disabled
        let promise;
        expect(self.problem.submitButton).toHaveAttr("disabled");
        expect(self.problem.submitButtonLabel.text()).toBe("Submit");
        callback({ success: "correct", html: curr_html });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      // Submit should be disabled
      expect(this.problem.submitButton).toHaveAttr("disabled");
      this.problem.reset();
      // Submit should remain disabled
      expect(self.problem.submitButton).toHaveAttr("disabled");
      expect(self.problem.submitButtonLabel.text()).toBe("Submit");
    });
  });

  describe("show problem with column in id", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.el.prepend('<div id="answer_1_1:11" /><div id="answer_1_2:12" />');
    });

    it("log the problem_show event", function () {
      this.problem.show();
      expect(Logger.log).toHaveBeenCalledWith("problem_show", { problem: "i4x://edX/101/problem/Problem1" });
    });

    it("fetch the answers", function () {
      spyOn($, "postWithPrefix");
      this.problem.show();
      expect($.postWithPrefix).toHaveBeenCalledWith("/problem/Problem1/problem_show", jasmine.any(Function));
    });

    it("show the answers", function () {
      spyOn($, "postWithPrefix").and.callFake((url, callback) =>
        callback({ answers: { "1_1:11": "One", "1_2:12": "Two" } }),
      );
      this.problem.show();
      expect($("#answer_1_1\\:11")).toHaveHtml("One");
      expect($("#answer_1_2\\:12")).toHaveHtml("Two");
    });

    it("disables the show answer button", function () {
      spyOn($, "postWithPrefix").and.callFake((url, callback) => callback({ answers: {} }));
      this.problem.show();
      expect(this.problem.el.find(".show").attr("disabled")).toEqual("disabled");
    });
  });

  describe("show", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.el.prepend('<div id="answer_1_1" /><div id="answer_1_2" />');
    });

    describe("when the answer has not yet shown", function () {
      beforeEach(function () {
        expect(this.problem.el.find(".show").attr("disabled")).not.toEqual("disabled");
      });

      it("log the problem_show event", function () {
        this.problem.show();
        expect(Logger.log).toHaveBeenCalledWith("problem_show", { problem: "i4x://edX/101/problem/Problem1" });
      });

      it("fetch the answers", function () {
        spyOn($, "postWithPrefix");
        this.problem.show();
        expect($.postWithPrefix).toHaveBeenCalledWith("/problem/Problem1/problem_show", jasmine.any(Function));
      });

      it("show the answers", function () {
        spyOn($, "postWithPrefix").and.callFake((url, callback) =>
          callback({ answers: { "1_1": "One", "1_2": "Two" } }),
        );
        this.problem.show();
        expect($("#answer_1_1")).toHaveHtml("One");
        expect($("#answer_1_2")).toHaveHtml("Two");
      });

      it("disables the show answer button", function () {
        spyOn($, "postWithPrefix").and.callFake((url, callback) => callback({ answers: {} }));
        this.problem.show();
        expect(this.problem.el.find(".show").attr("disabled")).toEqual("disabled");
      });

      describe("radio text question", function () {
        const radio_text_xml = `\
<section class="problem">
  <div><p></p><span><section id="choicetextinput_1_2_1" class="choicetextinput">

<form class="choicetextgroup capa_inputtype" id="inputtype_1_2_1">
  <div class="indicator-container">
    <span class="unanswered" style="display:inline-block;" id="status_1_2_1"></span>
  </div>
  <fieldset>
    <section id="forinput1_2_1_choiceinput_0bc">
      <input class="ctinput" type="radio" name="choiceinput_1_2_1" id="1_2_1_choiceinput_0bc" value="choiceinput_0"">
      <input class="ctinput" type="text" name="choiceinput_0_textinput_0" id="1_2_1_choiceinput_0_textinput_0" value=" ">
      <p id="answer_1_2_1_choiceinput_0bc" class="answer"></p>
    </>
    <section id="forinput1_2_1_choiceinput_1bc">
      <input class="ctinput" type="radio" name="choiceinput_1_2_1" id="1_2_1_choiceinput_1bc" value="choiceinput_1" >
      <input class="ctinput" type="text" name="choiceinput_1_textinput_0" id="1_2_1_choiceinput_1_textinput_0" value=" " >
      <p id="answer_1_2_1_choiceinput_1bc" class="answer"></p>
    </section>
    <section id="forinput1_2_1_choiceinput_2bc">
      <input class="ctinput" type="radio" name="choiceinput_1_2_1" id="1_2_1_choiceinput_2bc" value="choiceinput_2" >
      <input class="ctinput" type="text" name="choiceinput_2_textinput_0" id="1_2_1_choiceinput_2_textinput_0" value=" " >
      <p id="answer_1_2_1_choiceinput_2bc" class="answer"></p>
    </section></fieldset><input class="choicetextvalue" type="hidden" name="input_1_2_1" id="input_1_2_1"></form>
</section></span></div>
</section>\
`;
        beforeEach(function () {
          // Append a radiotextresponse problem to the problem, so we can check it's javascript functionality
          this.problem.el.prepend(radio_text_xml);
        });

        it("sets the correct class on the section for the correct choice", function () {
          spyOn($, "postWithPrefix").and.callFake((url, callback) =>
            callback({ answers: { "1_2_1": ["1_2_1_choiceinput_0bc"], "1_2_1_choiceinput_0bc": "3" } }),
          );
          this.problem.show();

          expect($("#forinput1_2_1_choiceinput_0bc").attr("class")).toEqual("choicetextgroup_show_correct");
          expect($("#answer_1_2_1_choiceinput_0bc").text()).toEqual("3");
          expect($("#answer_1_2_1_choiceinput_1bc").text()).toEqual("");
          expect($("#answer_1_2_1_choiceinput_2bc").text()).toEqual("");
        });

        it("Should not disable input fields", function () {
          spyOn($, "postWithPrefix").and.callFake((url, callback) =>
            callback({ answers: { "1_2_1": ["1_2_1_choiceinput_0bc"], "1_2_1_choiceinput_0bc": "3" } }),
          );
          this.problem.show();
          expect($("input#1_2_1_choiceinput_0bc").attr("disabled")).not.toEqual("disabled");
          expect($("input#1_2_1_choiceinput_1bc").attr("disabled")).not.toEqual("disabled");
          expect($("input#1_2_1_choiceinput_2bc").attr("disabled")).not.toEqual("disabled");
          expect($("input#1_2_1").attr("disabled")).not.toEqual("disabled");
        });
      });

      describe("imageinput", function () {
        let el, height, width;
        const imageinput_html = readFixtures("imageinput.underscore");

        const DEFAULTS = {
          id: "12345",
          width: "300",
          height: "400",
        };

        beforeEach(function () {
          this.problem = new Problem($(".xblock-student_view"));
          this.problem.el.prepend(_.template(imageinput_html)(DEFAULTS));
        });

        const assertAnswer = (problem, data) => {
          stubRequest(data);
          problem.show();

          $.each(data["answers"], (id, answer) => {
            const img = getImage(answer);
            el = $(`#inputtype_${id}`);
            expect(img).toImageDiffEqual(el.find("canvas")[0]);
          });
        };

        var stubRequest = (data) => {
          spyOn($, "postWithPrefix").and.callFake((url, callback) => callback(data));
        };

        var getImage = (coords, c_width, c_height) => {
          let ctx, reg;
          const types = {
            rectangle: (coords) => {
              reg = /^\(([0-9]+),([0-9]+)\)-\(([0-9]+),([0-9]+)\)$/;
              const rects = coords.replace(/\s*/g, "").split(/;/);

              $.each(rects, (index, rect) => {
                const { abs } = Math;
                const points = reg.exec(rect);
                if (points) {
                  width = abs(points[3] - points[1]);
                  height = abs(points[4] - points[2]);

                  return ctx.rect(points[1], points[2], width, height);
                }
              });

              ctx.stroke();
              ctx.fill();
            },

            regions: (coords) => {
              const parseCoords = (coords) => {
                reg = JSON.parse(coords);

                if (typeof reg[0][0][0] === "undefined") {
                  reg = [reg];
                }

                return reg;
              };

              return $.each(parseCoords(coords), (index, region) => {
                ctx.beginPath();
                $.each(region, (index, point) => {
                  if (index === 0) {
                    return ctx.moveTo(point[0], point[1]);
                  } else {
                    return ctx.lineTo(point[0], point[1]);
                  }
                });

                ctx.closePath();
                ctx.stroke();
                ctx.fill();
              });
            },
          };

          const canvas = document.createElement("canvas");
          canvas.width = c_width || 100;
          canvas.height = c_height || 100;

          if (canvas.getContext) {
            ctx = canvas.getContext("2d");
          } else {
            console.log("Canvas is not supported.");
          }

          ctx.fillStyle = "rgba(255,255,255,.3)";
          ctx.strokeStyle = "#FF0000";
          ctx.lineWidth = "2";

          $.each(coords, (key, value) => {
            if (types[key] != null && value) {
              return types[key](value);
            }
          });

          return canvas;
        };

        it("rectangle is drawn correctly", function () {
          assertAnswer(this.problem, {
            answers: {
              12345: {
                rectangle: "(10,10)-(30,30)",
                regions: null,
              },
            },
          });
        });

        it("region is drawn correctly", function () {
          assertAnswer(this.problem, {
            answers: {
              12345: {
                rectangle: null,
                regions: "[[10,10],[30,30],[70,30],[20,30]]",
              },
            },
          });
        });

        it("mixed shapes are drawn correctly", function () {
          assertAnswer(this.problem, {
            answers: {
              12345: {
                rectangle: "(10,10)-(30,30);(5,5)-(20,20)",
                regions: `[
  [[50,50],[40,40],[70,30],[50,70]],
  [[90,95],[95,95],[90,70],[70,70]]
]`,
              },
            },
          });
        });

        it("multiple image inputs draw answers on separate canvases", function () {
          const data = {
            id: "67890",
            width: "400",
            height: "300",
          };

          this.problem.el.prepend(_.template(imageinput_html)(data));
          assertAnswer(this.problem, {
            answers: {
              12345: {
                rectangle: null,
                regions: "[[10,10],[30,30],[70,30],[20,30]]",
              },
              67890: {
                rectangle: "(10,10)-(30,30)",
                regions: null,
              },
            },
          });
        });

        it("dictionary with answers doesn't contain answer for current id", function () {
          spyOn(console, "log");
          stubRequest({ answers: {} });
          this.problem.show();
          el = $("#inputtype_12345");
          expect(el.find("canvas")).not.toExist();
          expect(console.log).toHaveBeenCalledWith("Answer is absent for image input with id=12345");
        });
      });
    });
  });

  describe("save", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.answers = "foo=1&bar=2";
    });

    it("log the problem_save event", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.save();
      expect(Logger.log).toHaveBeenCalledWith("problem_save", "foo=1&bar=2");
    });

    it("POST to save problem", function () {
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        let promise;
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      this.problem.save();
      expect($.postWithPrefix).toHaveBeenCalledWith(
        "/problem/Problem1/problem_save",
        "foo=1&bar=2",
        jasmine.any(Function),
      );
    });

    it("tests that save does not enable the submit button or change the text when submit is originally disabled", function () {
      const self = this;
      const curr_html = this.problem.el.html();
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        // enableButtons should have been called at this point and the submit button should be unaffected
        let promise;
        expect(self.problem.submitButton).toHaveAttr("disabled");
        expect(self.problem.submitButtonLabel.text()).toBe("Submit");
        callback({ success: "correct", html: curr_html });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      // Expect submit to be disabled and labeled properly at the start
      expect(this.problem.submitButton).toHaveAttr("disabled");
      expect(this.problem.submitButtonLabel.text()).toBe("Submit");
      this.problem.save();
      // Submit button should have the same state after save has completed
      expect(this.problem.submitButton).toHaveAttr("disabled");
      expect(this.problem.submitButtonLabel.text()).toBe("Submit");
    });

    it("tests that save does not disable the submit button or change the text when submit is originally enabled", function () {
      const self = this;
      const curr_html = this.problem.el.html();
      spyOn($, "postWithPrefix").and.callFake(function (url, answers, callback) {
        // enableButtons should have been called at this point, and the submit button should be disabled while submitting
        let promise;
        expect(self.problem.submitButton).toHaveAttr("disabled");
        expect(self.problem.submitButtonLabel.text()).toBe("Submit");
        callback({ success: "correct", html: curr_html });
        promise = {
          always(callable) {
            return callable();
          },
        };
        return promise;
      });
      // Expect submit to be enabled and labeled properly at the start after adding an input
      $("#input_example_1").val("test").trigger("input");
      expect(this.problem.submitButton).not.toHaveAttr("disabled");
      expect(this.problem.submitButtonLabel.text()).toBe("Submit");
      this.problem.save();
      // Submit button should have the same state after save has completed
      expect(this.problem.submitButton).not.toHaveAttr("disabled");
      expect(this.problem.submitButtonLabel.text()).toBe("Submit");
    });
  });

  describe("refreshMath", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      // Reset Queue spy so that bind()'s Queue call ([fn, null, domEl]) is not
      // included when toHaveBeenCalledWith scans recorded calls. In Jasmine 2.99,
      // toHaveBeenCalledWith iterates ALL recorded calls' args element-by-element
      // (even mismatched ones, for diff output). jasmine-jquery's custom equality
      // tester calls $(domEl).is(anyString) when comparing a DOM node against a
      // string — which throws a Sizzle syntax error if the string isn't a valid
      // CSS selector (e.g. "E=mc^2"). Resetting here isolates this describe to
      // testing only what refreshMath itself queues.
      MathJax.Hub.Queue.calls.reset();
      $("#input_example_1").val("E=mc^2");
      this.problem.refreshMath({ target: $("#input_example_1").get(0) });
    });

    it("should queue the conversion and MathML element update", function () {
      expect(MathJax.Hub.Queue).toHaveBeenCalledWith(
        ["Text", this.stubbedJax, "E=mc^2"],
        [this.problem.updateMathML, this.stubbedJax, $("#input_example_1").get(0)],
      );
    });
  });

  describe("updateMathML", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.stubbedJax.root.toMathML.and.returnValue("<MathML>");
    });

    describe("when there is no exception", function () {
      beforeEach(function () {
        this.problem.updateMathML(this.stubbedJax, $("#input_example_1").get(0));
      });

      it("convert jax to MathML", () => expect($("#input_example_1_dynamath")).toHaveValue("<MathML>"));
    });

    describe("when there is an exception", function () {
      beforeEach(function () {
        const error = new Error();
        error.restart = true;
        this.stubbedJax.root.toMathML.and.throwError(error);
        this.problem.updateMathML(this.stubbedJax, $("#input_example_1").get(0));
      });

      it("should queue up the exception", function () {
        expect(MathJax.Callback.After).toHaveBeenCalledWith([this.problem.refreshMath, this.stubbedJax], true);
      });
    });
  });

  describe("refreshAnswers", function () {
    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.el.html(`\
<textarea class="CodeMirror" />
<input id="input_1_1" name="input_1_1" class="schematic" value="one" />
<input id="input_1_2" name="input_1_2" value="two" />
<input id="input_bogus_3" name="input_bogus_3" value="three" />\
`);
      this.stubSchematic = { update_value: jasmine.createSpy("schematic") };
      this.stubCodeMirror = { save: jasmine.createSpy("CodeMirror") };
      $("input.schematic").get(0).schematic = this.stubSchematic;
      $("textarea.CodeMirror").get(0).CodeMirror = this.stubCodeMirror;
    });

    it("update each schematic", function () {
      this.problem.refreshAnswers();
      expect(this.stubSchematic.update_value).toHaveBeenCalled();
    });

    it("update each code block", function () {
      this.problem.refreshAnswers();
      expect(this.stubCodeMirror.save).toHaveBeenCalled();
    });
  });

  describe("multiple JsInput in single problem", function () {
    const jsinput_html = readFixtures("jsinput_problem.html");

    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.render(jsinput_html);
    });

    it("submit_save_waitfor should return false", function () {
      $(this.problem.inputs[0]).data("waitfor", function () {});
      expect(this.problem.submit_save_waitfor()).toEqual(false);
    });
  });

  describe("Submitting an xqueue-graded problem", function () {
    const matlabinput_html = readFixtures("matlabinput_problem.html");

    beforeEach(function () {
      spyOn($, "postWithPrefix").and.callFake((url, callback) => callback({ html: matlabinput_html }));
      jasmine.clock().install();
      this.problem = new Problem($(".xblock-student_view"));
      spyOn(this.problem, "poll").and.callThrough();
      this.problem.render(matlabinput_html);
    });

    afterEach(() => jasmine.clock().uninstall());

    it("check that we stop polling after a fixed amount of time", function () {
      expect(this.problem.poll).not.toHaveBeenCalled();
      jasmine.clock().tick(1);
      const time_steps = [1000, 2000, 4000, 8000, 16000, 32000];
      let num_calls = 1;
      for (let time_step of Array.from(time_steps)) {
        ((time_step) => {
          jasmine.clock().tick(time_step);
          expect(this.problem.poll.calls.count()).toEqual(num_calls);
          num_calls += 1;
        })(time_step);
      }

      // jump the next step and verify that we are not still continuing to poll
      jasmine.clock().tick(64000);
      expect(this.problem.poll.calls.count()).toEqual(6);

      expect($(".notification-gentle-alert .notification-message").text()).toEqual(
        "The grading process is still running. Refresh the page to see updates.",
      );
    });
  });

  describe("codeinput problem", function () {
    const codeinputProblemHtml = readFixtures("codeinput_problem.html");

    beforeEach(function () {
      spyOn($, "postWithPrefix").and.callFake((url, callback) => callback({ html: codeinputProblemHtml }));
      this.problem = new Problem($(".xblock-student_view"));
      this.problem.render(codeinputProblemHtml);
    });

    it("has rendered with correct a11y info", function () {
      const CodeMirrorTextArea = $("textarea")[1];
      const CodeMirrorTextAreaId = "cm-textarea-101";

      // verify that question label has correct `for` attribute value
      expect($(".problem-group-label").attr("for")).toEqual(CodeMirrorTextAreaId);

      // verify that codemirror textarea has correct `id` attribute value
      expect($(CodeMirrorTextArea).attr("id")).toEqual(CodeMirrorTextAreaId);

      // verify that codemirror textarea has correct `aria-describedby` attribute value
      expect($(CodeMirrorTextArea).attr("aria-describedby")).toEqual("cm-editor-exit-message-101 status_101");
    });
  });

  describe("show answer button", function () {
    const radioButtonProblemHtml = readFixtures("radiobutton_problem.html");
    const checkboxProblemHtml = readFixtures("checkbox_problem.html");

    beforeEach(function () {
      this.problem = new Problem($(".xblock-student_view"));

      this.checkAssertionsAfterClickingAnotherOption = () => {
        // verify that 'show answer button is no longer disabled'
        expect(this.problem.el.find(".show").attr("disabled")).not.toEqual("disabled");

        // verify that displayed answer disappears
        expect(this.problem.el.find("div.choicegroup")).not.toHaveClass("choicegroup_correct");

        // verify that radio/checkbox label has no span having class '.status.correct'
        expect(this.problem.el.find("div.choicegroup")).not.toHaveAttr("span.status.correct");
      };
    });

    it("should become enabled after a radiobutton is selected", function () {
      $("#input_example_1").replaceWith(radioButtonProblemHtml);
      // assume that 'ShowAnswer' button is clicked,
      // clicking make it disabled.
      this.problem.el.find(".show").attr("disabled", "disabled");
      // bind click event to input fields
      this.problem.submitAnswersAndSubmitButton(true);
      // selects option 2
      $("#input_1_1_2").attr("checked", true).trigger("click");
      this.checkAssertionsAfterClickingAnotherOption();
    });

    it("should become enabled after a checkbox is selected", function () {
      $("#input_example_1").replaceWith(checkboxProblemHtml);
      this.problem.el.find(".show").attr("disabled", "disabled");
      this.problem.submitAnswersAndSubmitButton(true);
      $("#input_1_1_2").click();
      this.checkAssertionsAfterClickingAnotherOption();
    });
  });
});
