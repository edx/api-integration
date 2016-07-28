"""
Some test content strings. Best to keep them out of the test files because they take up a lot of
text space
"""

from textwrap import dedent

TEST_COURSE_UPDATES_CONTENT = dedent(
    """
    <section aria-labelledby="course-updates-heading">
        <h2 class="hd hd-2 sr" id="course-updates-heading">All course updates</h2>
    <div class="recent-updates">
        <article class="updates-article">
            <h2 class="date" id="msg-date-0">April 18, 2014</h2>
            <button
                class="toggle-visibility-button"
                data-hide="Hide"
                data-show="Show"
                aria-describedby="msg-date-0"
                aria-controls="msg-content-0"
                aria-expanded="true"
            ></button>
          <div class="toggle-visibility-element article-content " id="msg-content-0">
            This does not have a paragraph tag around it
          </div>
        </article>
        <article class="updates-article">
            <h2 class="date" id="msg-date-1">April 17, 2014</h2>
            <button
                class="toggle-visibility-button"
                data-hide="Hide"
                data-show="Show"
                aria-describedby="msg-date-1"
                aria-controls="msg-content-1"
                aria-expanded="true"
            ></button>
          <div class="toggle-visibility-element article-content hidden" id="msg-content-1">
            Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag
          </div>
        </article>
        <article class="updates-article">
            <h2 class="date" id="msg-date-2">April 16, 2014</h2>
            <button
                class="toggle-visibility-button"
                data-hide="Hide"
                data-show="Show"
                aria-describedby="msg-date-2"
                aria-controls="msg-content-2"
                aria-expanded="true"
            ></button>
          <div class="toggle-visibility-element article-content hidden" id="msg-content-2">
            Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag<p>one more</p>
          </div>
        </article>
    </div>

    <button
        class="toggle-visibility-button show-older-updates"
        data-hide=""
        data-show="Show Earlier Course Updates"
        aria-expanded="false"
        aria-controls="old-updates"
    ></button>

    <div class="old-updates hidden toggle-visibility-element" id="old-updates">
        <article class="updates-article">
          <h2 class="date" id="msg-date-3">April 15, 2014</h2>
          <button
              class="toggle-visibility-button"
              data-hide="Hide"
              data-show="Show"
              aria-describedby="msg-date-3"
              aria-controls="msg-content-3"
              aria-expanded="false"
          ></button>
          <div class="toggle-visibility-element article-content hidden" id="msg-content-3"><p>A perfectly</p><p>formatted piece</p><p>of HTML</p></div>
        </article>
    </div>
    </section>
    """
)

TEST_COURSE_UPDATES_CONTENT_LEGACY = dedent(
    """
    <ol>
      <li>
        <h2>April 18, 2014</h2>
        This is some legacy content
      </li>
      <li>
        <h2>April 17, 2014</h2>
        Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag
      </li>
      <li>
        <h2>April 16, 2014</h2>
        Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag<p>one more</p>
      </li>
      <li>
        <h2>April 15, 2014</h2>
        <p>A perfectly</p><p>formatted piece</p><p>of HTML</p>
      </li>
    </ol>
    """
)

TEST_STATIC_TAB1_CONTENT = dedent(
    """
    <div>This is static tab1</div>
    """
)

TEST_STATIC_TAB2_CONTENT = dedent(
    """
    <div>
    This is static tab2 with content size greater than 200 bytes to test static tab content cache max size limit
    </div>
    """
)

TEST_COURSE_OVERVIEW_CONTENT = dedent(
    """
    <section class="about">
      <h2>About This Course</h2>
      <p>Include your long course description here. The long course description should contain 150-400 words.</p>

      <p>This is paragraph 2 of the long course description. Add more paragraphs as needed.
          Make sure to enclose them in paragraph tags.</p>
    </section>

    <section class="prerequisites">
      <h2>Prerequisites</h2>
      <p>Add information about course prerequisites here.</p>
    </section>

    <section class="course-staff">
      <h2>Course Staff</h2>
      <article class="teacher">
        <div class="teacher-image">
          <img src="/images/pl-faculty.png" align="left" style="margin:0 20 px 0" alt="Course Staff Image #1">
        </div>
        <h3>Staff Member #1</h3>
        <p>Biography of instructor/staff member #1</p>
      </article>

      <article class="teacher">
        <div class="teacher-image">
          <img src="/images/pl-faculty.png" align="left" style="margin:0 20 px 0" alt="Course Staff Image #2">
        </div>
        <h3>Staff Member #2</h3>
        <p>Biography of instructor/staff member #2</p>
      </article>

      <article class="author">
        <div class="author-image">
          <img src="/images/pl-author.png" align="left" style="margin:0 20 px 0" alt="Author Name">
        </div>
        <h3>Author Name</h3>
        <p>Biography of Author Name</p>
      </article>
    </section>

    <section class="faq">
        <p>Some text here</p>
    </section>

    <section class="intro-video" data-videoid="foobar">
    </section>
    """
)
