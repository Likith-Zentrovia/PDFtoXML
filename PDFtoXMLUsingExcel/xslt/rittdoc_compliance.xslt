<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">

    <!--
        XSLT Transformation for RittDoc DTD Compliance

        This stylesheet transforms generic DocBook XML to RittDoc DTD compliant format.

        Key transformations:
        1. Convert <info> to <bookinfo>
        2. Convert generic <section> to numbered <sect1>, <sect2>, etc.
        3. Ensure <copyright> contains <year> element
        4. Ensure proper element nesting
        5. Remove non-compliant elements
    -->

    <!-- Output settings -->
    <xsl:output
        method="xml"
        encoding="UTF-8"
        indent="yes"
        omit-xml-declaration="no"/>

    <!-- Identity template - copy everything by default -->
    <xsl:template match="@*|node()">
        <xsl:copy>
            <xsl:apply-templates select="@*|node()"/>
        </xsl:copy>
    </xsl:template>

    <!-- Transform <info> to <bookinfo> (RittDoc uses DocBook 4.x style) -->
    <xsl:template match="info">
        <bookinfo>
            <xsl:apply-templates select="@*|node()"/>
        </bookinfo>
    </xsl:template>

    <!-- Transform generic <section> to numbered sect1-sect5 based on nesting depth -->
    <!-- Also auto-generates ID attributes using chapter/section numbering -->
    <xsl:template match="section">
        <xsl:variable name="depth">
            <xsl:value-of select="count(ancestor::section) + 1"/>
        </xsl:variable>

        <!-- Generate ID based on chapter and section hierarchy -->
        <xsl:variable name="generated-id">
            <xsl:call-template name="generate-section-id"/>
        </xsl:variable>

        <xsl:choose>
            <xsl:when test="$depth = 1">
                <sect1>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect1>
            </xsl:when>
            <xsl:when test="$depth = 2">
                <sect2>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect2>
            </xsl:when>
            <xsl:when test="$depth = 3">
                <sect3>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect3>
            </xsl:when>
            <xsl:when test="$depth = 4">
                <sect4>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect4>
            </xsl:when>
            <xsl:when test="$depth = 5">
                <sect5>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect5>
            </xsl:when>
            <xsl:otherwise>
                <!-- If nesting exceeds 5 levels, keep as sect5 and warn via comment -->
                <xsl:comment>Warning: Section nesting exceeds 5 levels - converted to sect5</xsl:comment>
                <sect5>
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:apply-templates select="@*|node()"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>
                            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
                        </xsl:otherwise>
                    </xsl:choose>
                </sect5>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <!-- Template to generate section IDs based on chapter and section numbering -->
    <xsl:template name="generate-section-id">
        <!-- Find the ancestor chapter -->
        <xsl:variable name="chapter-num">
            <xsl:choose>
                <xsl:when test="ancestor::chapter">
                    <xsl:number count="chapter" from="book|part" level="any" format="01"/>
                </xsl:when>
                <xsl:when test="ancestor::appendix">
                    <xsl:text>App</xsl:text>
                    <xsl:number count="appendix" from="book" level="any" format="A"/>
                </xsl:when>
                <xsl:otherwise>
                    <xsl:text>00</xsl:text>
                </xsl:otherwise>
            </xsl:choose>
        </xsl:variable>

        <!-- Build section hierarchy path -->
        <xsl:text>Ch</xsl:text>
        <xsl:value-of select="$chapter-num"/>
        <xsl:text>Sec</xsl:text>

        <!-- Count positions at each level for section elements -->
        <xsl:for-each select="ancestor-or-self::section">
            <xsl:number count="section" level="single" format="01"/>
        </xsl:for-each>

        <!-- For sect1-sect5 elements (not section), count their positions -->
        <xsl:if test="not(self::section)">
            <xsl:choose>
                <xsl:when test="self::sect1">
                    <xsl:number count="sect1|section[count(ancestor::section) = 0]" level="single" format="01"/>
                </xsl:when>
                <xsl:when test="self::sect2">
                    <xsl:for-each select="ancestor::sect1|ancestor::section[count(ancestor::section) = 0]">
                        <xsl:number count="sect1|section[count(ancestor::section) = 0]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:number count="sect2|section[count(ancestor::section) = 1]" level="single" format="01"/>
                </xsl:when>
                <xsl:when test="self::sect3">
                    <xsl:for-each select="ancestor::sect1|ancestor::section[count(ancestor::section) = 0]">
                        <xsl:number count="sect1|section[count(ancestor::section) = 0]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect2|ancestor::section[count(ancestor::section) = 1]">
                        <xsl:number count="sect2|section[count(ancestor::section) = 1]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:number count="sect3|section[count(ancestor::section) = 2]" level="single" format="01"/>
                </xsl:when>
                <xsl:when test="self::sect4">
                    <xsl:for-each select="ancestor::sect1|ancestor::section[count(ancestor::section) = 0]">
                        <xsl:number count="sect1|section[count(ancestor::section) = 0]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect2|ancestor::section[count(ancestor::section) = 1]">
                        <xsl:number count="sect2|section[count(ancestor::section) = 1]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect3|ancestor::section[count(ancestor::section) = 2]">
                        <xsl:number count="sect3|section[count(ancestor::section) = 2]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:number count="sect4|section[count(ancestor::section) = 3]" level="single" format="01"/>
                </xsl:when>
                <xsl:when test="self::sect5">
                    <xsl:for-each select="ancestor::sect1|ancestor::section[count(ancestor::section) = 0]">
                        <xsl:number count="sect1|section[count(ancestor::section) = 0]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect2|ancestor::section[count(ancestor::section) = 1]">
                        <xsl:number count="sect2|section[count(ancestor::section) = 1]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect3|ancestor::section[count(ancestor::section) = 2]">
                        <xsl:number count="sect3|section[count(ancestor::section) = 2]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:for-each select="ancestor::sect4|ancestor::section[count(ancestor::section) = 3]">
                        <xsl:number count="sect4|section[count(ancestor::section) = 3]" level="single" format="01"/>
                    </xsl:for-each>
                    <xsl:number count="sect5|section[count(ancestor::section) = 4]" level="single" format="01"/>
                </xsl:when>
            </xsl:choose>
        </xsl:if>
    </xsl:template>

    <!-- Ensure <copyright> always has <year> element -->
    <xsl:template match="copyright">
        <copyright>
            <xsl:apply-templates select="@*"/>

            <!-- Copy existing year or add placeholder -->
            <xsl:choose>
                <xsl:when test="year">
                    <xsl:apply-templates select="year"/>
                </xsl:when>
                <xsl:otherwise>
                    <year>
                        <xsl:text>2024</xsl:text>
                    </year>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy other child elements -->
            <xsl:apply-templates select="node()[not(self::year)]"/>
        </copyright>
    </xsl:template>

    <!-- Ensure <bookinfo>/<info> has required elements -->
    <xsl:template match="bookinfo">
        <bookinfo>
            <xsl:apply-templates select="@*"/>

            <!-- Ensure title exists -->
            <xsl:choose>
                <xsl:when test="title">
                    <xsl:apply-templates select="title"/>
                </xsl:when>
                <xsl:otherwise>
                    <title>Untitled Book</title>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy subtitle if exists -->
            <xsl:apply-templates select="subtitle"/>

            <!-- Ensure at least one author/authorgroup -->
            <xsl:choose>
                <xsl:when test="author or authorgroup">
                    <xsl:apply-templates select="author|authorgroup"/>
                </xsl:when>
                <xsl:otherwise>
                    <authorgroup>
                        <author>
                            <personname>
                                <surname>Unknown Author</surname>
                            </personname>
                        </author>
                    </authorgroup>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Ensure publisher -->
            <xsl:choose>
                <xsl:when test="publisher">
                    <xsl:apply-templates select="publisher"/>
                </xsl:when>
                <xsl:otherwise>
                    <publisher>
                        <publishername>Unknown Publisher</publishername>
                    </publisher>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy ISBN if exists, otherwise add placeholder -->
            <xsl:choose>
                <xsl:when test="isbn">
                    <xsl:apply-templates select="isbn"/>
                </xsl:when>
                <xsl:otherwise>
                    <isbn>0000000000000</isbn>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy pubdate if exists -->
            <xsl:choose>
                <xsl:when test="pubdate">
                    <xsl:apply-templates select="pubdate"/>
                </xsl:when>
                <xsl:otherwise>
                    <pubdate>2024</pubdate>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy edition if exists -->
            <xsl:choose>
                <xsl:when test="edition">
                    <xsl:apply-templates select="edition"/>
                </xsl:when>
                <xsl:otherwise>
                    <edition>1st Edition</edition>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Ensure copyright with year -->
            <xsl:choose>
                <xsl:when test="copyright">
                    <xsl:apply-templates select="copyright"/>
                </xsl:when>
                <xsl:otherwise>
                    <copyright>
                        <year>2024</year>
                        <holder>Copyright Holder</holder>
                    </copyright>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy any remaining elements not already processed -->
            <xsl:apply-templates select="node()[
                not(self::title) and
                not(self::subtitle) and
                not(self::author) and
                not(self::authorgroup) and
                not(self::publisher) and
                not(self::isbn) and
                not(self::pubdate) and
                not(self::edition) and
                not(self::copyright)
            ]"/>
        </bookinfo>
    </xsl:template>

    <!-- Strip out HTML5 elements that aren't in DocBook 4.x -->
    <xsl:template match="article[@xmlns='http://www.w3.org/1999/xhtml']">
        <!-- Convert HTML article to DocBook chapter -->
        <chapter>
            <xsl:apply-templates select="@*[local-name() != 'xmlns']|node()"/>
        </chapter>
    </xsl:template>

    <!-- Remove namespace declarations from elements -->
    <xsl:template match="@xmlns">
        <!-- Strip xmlns attributes -->
    </xsl:template>

    <!-- Ensure proper author structure -->
    <xsl:template match="author[not(personname) and (firstname or surname)]">
        <author>
            <xsl:apply-templates select="@*"/>
            <personname>
                <xsl:apply-templates select="firstname|surname"/>
            </personname>
            <xsl:apply-templates select="node()[not(self::firstname) and not(self::surname)]"/>
        </author>
    </xsl:template>

    <!-- Convert GLOSSARY chapters to proper glossary elements -->
    <!-- This fixes "invalid content model" errors when chapters contain glosslist -->
    <xsl:template match="chapter[translate(title, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') = 'glossary']">
        <glossary>
            <xsl:apply-templates select="@*"/>
            <!-- Copy title and other header elements -->
            <xsl:apply-templates select="title|subtitle|titleabbrev|glossaryinfo"/>
            <!-- Unwrap glosslist - glossary takes glossentry directly -->
            <xsl:apply-templates select="glosslist/glossentry"/>
            <!-- Also handle direct glossentry children (if any) -->
            <xsl:apply-templates select="glossentry"/>
            <!-- Handle any variablelist that will be converted to glosslist -->
            <xsl:apply-templates select="variablelist/varlistentry" mode="to-glossentry"/>
        </glossary>
    </xsl:template>

    <!-- Convert varlistentry to glossentry when in GLOSSARY context -->
    <xsl:template match="varlistentry" mode="to-glossentry">
        <glossentry>
            <xsl:apply-templates select="@*"/>
            <!-- Convert term to glossterm -->
            <xsl:apply-templates select="term" mode="glossterm"/>
            <!-- Convert listitem to glossdef -->
            <xsl:apply-templates select="listitem" mode="glossdef"/>
            <!-- Handle any other children -->
            <xsl:apply-templates select="node()[not(self::term) and not(self::listitem)]"/>
        </glossentry>
    </xsl:template>

    <!-- Convert BANNED variablelist to glosslist -->
    <xsl:template match="variablelist">
        <glosslist>
            <xsl:apply-templates select="@*|node()"/>
        </glosslist>
    </xsl:template>

    <!-- Convert BANNED varlistentry to glossentry -->
    <xsl:template match="varlistentry">
        <glossentry>
            <xsl:apply-templates select="@*"/>
            <!-- Convert term to glossterm -->
            <xsl:apply-templates select="term" mode="glossterm"/>
            <!-- Convert listitem to glossdef -->
            <xsl:apply-templates select="listitem" mode="glossdef"/>
            <!-- Handle any other children -->
            <xsl:apply-templates select="node()[not(self::term) and not(self::listitem)]"/>
        </glossentry>
    </xsl:template>

    <!-- Convert term to glossterm -->
    <xsl:template match="term" mode="glossterm">
        <glossterm>
            <xsl:apply-templates select="@*|node()"/>
        </glossterm>
    </xsl:template>

    <!-- Convert listitem to glossdef -->
    <xsl:template match="listitem" mode="glossdef">
        <glossdef>
            <xsl:apply-templates select="@*|node()"/>
        </glossdef>
    </xsl:template>

    <!-- Remove informalfigure (BANNED) - convert to figure -->
    <xsl:template match="informalfigure">
        <figure>
            <xsl:apply-templates select="@*"/>
            <!-- Add a generic title if missing -->
            <xsl:if test="not(title)">
                <title>Figure</title>
            </xsl:if>
            <xsl:apply-templates select="node()"/>
        </figure>
    </xsl:template>

    <!-- Remove informaltable (BANNED) - convert to table -->
    <xsl:template match="informaltable">
        <table>
            <xsl:apply-templates select="@*"/>
            <!-- Add a generic title if missing -->
            <xsl:if test="not(title)">
                <title>Table</title>
            </xsl:if>
            <xsl:apply-templates select="node()"/>
        </table>
    </xsl:template>

    <!-- Handle figure elements that contain table elements -->
    <!-- According to DTD, figures can only contain mediaobject/graphic, not table -->
    <!-- Convert such figures to standalone tables to maintain content -->
    <xsl:template match="figure[table]">
        <xsl:comment>Converted from figure to table for DTD compliance - figures cannot contain tables</xsl:comment>
        <xsl:choose>
            <!-- If figure contains ONLY a table (and title), convert to table -->
            <xsl:when test="count(table) = 1 and count(*[not(self::title) and not(self::table)]) = 0">
                <table>
                    <xsl:apply-templates select="@*[local-name() != 'id']"/>
                    <!-- Preserve or generate ID -->
                    <xsl:choose>
                        <xsl:when test="@id">
                            <xsl:attribute name="id"><xsl:value-of select="@id"/></xsl:attribute>
                        </xsl:when>
                        <xsl:when test="table/@id">
                            <xsl:attribute name="id"><xsl:value-of select="table/@id"/></xsl:attribute>
                        </xsl:when>
                    </xsl:choose>

                    <!-- Use figure's title if it exists, otherwise use table's title -->
                    <xsl:choose>
                        <xsl:when test="title">
                            <xsl:apply-templates select="title"/>
                        </xsl:when>
                        <xsl:when test="table/title">
                            <xsl:apply-templates select="table/title"/>
                        </xsl:when>
                        <xsl:otherwise>
                            <title>Table</title>
                        </xsl:otherwise>
                    </xsl:choose>

                    <!-- Copy table contents (excluding title as we already handled it) -->
                    <xsl:apply-templates select="table/node()[not(self::title)]"/>
                </table>
            </xsl:when>

            <!-- If figure contains table AND other content, keep both but separate them -->
            <xsl:otherwise>
                <!-- First output the table -->
                <xsl:for-each select="table">
                    <table>
                        <xsl:apply-templates select="@*"/>
                        <xsl:if test="not(title)">
                            <title>Table</title>
                        </xsl:if>
                        <xsl:apply-templates select="node()"/>
                    </table>
                </xsl:for-each>

                <!-- Then output remaining content as para if there's text -->
                <xsl:if test="count(*[not(self::title) and not(self::table)]) > 0 or normalize-space(text()) != ''">
                    <para>
                        <xsl:apply-templates select="text()|*[not(self::title) and not(self::table)]"/>
                    </para>
                </xsl:if>
            </xsl:otherwise>
        </xsl:choose>
    </xsl:template>

    <!-- Ensure all figures have required elements: title and mediaobject/graphic -->
    <!-- This template only applies to figures WITHOUT tables (handled above) -->
    <xsl:template match="figure[not(table)]">
        <figure>
            <xsl:apply-templates select="@*"/>

            <!-- Ensure title exists (MANDATORY per DTD) -->
            <xsl:choose>
                <xsl:when test="title">
                    <xsl:apply-templates select="title"/>
                </xsl:when>
                <xsl:otherwise>
                    <title>Figure</title>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy other children -->
            <xsl:apply-templates select="node()[not(self::title)]"/>

            <!-- Ensure at least one mediaobject or graphic exists (MANDATORY per DTD) -->
            <xsl:if test="not(mediaobject) and not(graphic) and not(screenshot) and not(programlisting)">
                <mediaobject>
                    <textobject>
                        <phrase>Content not available</phrase>
                    </textobject>
                </mediaobject>
            </xsl:if>
        </figure>
    </xsl:template>

    <!-- Ensure all tables have required elements: title and tgroup -->
    <!-- This only applies to tables NOT inside figures (those are handled separately) -->
    <xsl:template match="table[not(parent::figure)]">
        <table>
            <xsl:apply-templates select="@*"/>

            <!-- Ensure title exists (MANDATORY per DTD) -->
            <xsl:choose>
                <xsl:when test="title">
                    <xsl:apply-templates select="title"/>
                </xsl:when>
                <xsl:otherwise>
                    <title>Table</title>
                </xsl:otherwise>
            </xsl:choose>

            <!-- Copy other children -->
            <xsl:apply-templates select="node()[not(self::title)]"/>

            <!-- Ensure at least one tgroup, graphic, or mediaobject exists (MANDATORY per DTD) -->
            <xsl:if test="not(tgroup) and not(graphic) and not(mediaobject)">
                <tgroup cols="1">
                    <tbody>
                        <row>
                            <entry>
                                <para>Table content not available</para>
                            </entry>
                        </row>
                    </tbody>
                </tgroup>
            </xsl:if>
        </table>
    </xsl:template>

    <!-- Handle existing sect1-sect5 elements that don't have IDs -->
    <!-- Auto-generate IDs using chapter/section numbering convention -->
    <xsl:template match="sect1[not(@id)]|sect2[not(@id)]|sect3[not(@id)]|sect4[not(@id)]|sect5[not(@id)]">
        <xsl:element name="{local-name()}">
            <!-- Generate ID based on chapter and section hierarchy -->
            <xsl:variable name="generated-id">
                <xsl:call-template name="generate-section-id"/>
            </xsl:variable>
            <xsl:attribute name="id"><xsl:value-of select="$generated-id"/></xsl:attribute>

            <!-- Copy other attributes and all children -->
            <xsl:apply-templates select="@*[local-name() != 'id']|node()"/>
        </xsl:element>
    </xsl:template>

    <!-- Remove misplaced figure elements from book root -->
    <!-- According to DTD, figures should only appear within chapters/sections -->
    <xsl:template match="book/figure">
        <xsl:comment>Removed misplaced figure from book root - figures must be inside chapters/sections</xsl:comment>
        <xsl:comment>Original figure title: <xsl:value-of select="title"/></xsl:comment>
    </xsl:template>

    <!-- Also handle para elements that might contain misplaced figures in book root -->
    <xsl:template match="book/para[figure]">
        <!-- Keep para but remove figures -->
        <xsl:if test="text()[normalize-space() != ''] or *[not(self::figure)]">
            <xsl:comment>Removed figures from para in book root</xsl:comment>
        </xsl:if>
    </xsl:template>

    <!-- Handle anchor elements without IDs (ID is REQUIRED by DTD) -->
    <xsl:template match="anchor[not(@id)]">
        <anchor>
            <!-- Generate a unique ID for this anchor -->
            <xsl:attribute name="id">
                <xsl:text>anchor-</xsl:text>
                <xsl:choose>
                    <!-- Try to use context to make ID meaningful -->
                    <xsl:when test="ancestor::chapter">
                        <xsl:text>ch</xsl:text>
                        <xsl:number count="chapter" from="book|part" level="any" format="01"/>
                        <xsl:text>-</xsl:text>
                    </xsl:when>
                    <xsl:when test="ancestor::appendix">
                        <xsl:text>app</xsl:text>
                        <xsl:number count="appendix" from="book" level="any" format="a"/>
                        <xsl:text>-</xsl:text>
                    </xsl:when>
                </xsl:choose>
                <!-- Add position number to ensure uniqueness -->
                <xsl:number count="anchor" level="any" format="0001"/>
            </xsl:attribute>
            <!-- Copy other attributes -->
            <xsl:apply-templates select="@*[local-name() != 'id']"/>
        </anchor>
    </xsl:template>

    <!-- Ensure sections are not empty - they must have content -->
    <!-- Sections need: title + (divcomponent.mix+ or subsections+) -->
    <!-- This template adds a placeholder para if section has only a title -->
    <xsl:template match="sect1[not(*[not(self::title) and not(self::subtitle) and not(self::titleabbrev) and not(self::sect1info)])]
                       |sect2[not(*[not(self::title) and not(self::subtitle) and not(self::titleabbrev) and not(self::sect2info)])]
                       |sect3[not(*[not(self::title) and not(self::subtitle) and not(self::titleabbrev) and not(self::sect3info)])]
                       |sect4[not(*[not(self::title) and not(self::subtitle) and not(self::titleabbrev) and not(self::sect4info)])]
                       |sect5[not(*[not(self::title) and not(self::subtitle) and not(self::titleabbrev) and not(self::sect5info)])]">
        <xsl:element name="{local-name()}">
            <!-- Copy all attributes -->
            <xsl:apply-templates select="@*"/>

            <!-- Copy info, title, subtitle, titleabbrev if present -->
            <xsl:apply-templates select="*[self::sect1info or self::sect2info or self::sect3info or self::sect4info or self::sect5info or self::title or self::subtitle or self::titleabbrev]"/>

            <!-- Add a placeholder para to make section non-empty -->
            <para>
                <xsl:text>Content placeholder - section cannot be empty per DTD requirements.</xsl:text>
            </para>

            <!-- Copy any other remaining children -->
            <xsl:apply-templates select="*[not(self::sect1info) and not(self::sect2info) and not(self::sect3info) and not(self::sect4info) and not(self::sect5info) and not(self::title) and not(self::subtitle) and not(self::titleabbrev)]"/>
        </xsl:element>
    </xsl:template>

</xsl:stylesheet>
