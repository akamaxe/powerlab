function export_refs(courseDir, outDir, aNumbers)
%EXPORT_REFS  Write the MATLAB reference solutions to JSON test fixtures.
%
%   export_refs(courseDir, outDir) runs the MATLAB solutions of the EG2100
%   assignments (S1, S2, S3, LF and the LF variant with the bus-5 shunt rated
%   at nominal voltage) for A = 80 and A = 0, and writes every numeric
%   variable of each script to outDir/refs_A<A>.json.
%
%   courseDir is the coursework folder that holds Assignment1/, Assignment2/,
%   AssignmentS3/ and AssignmentLF/, each with its matlab/ subfolder. The
%   original scripts are never modified: each run works on a temporary copy
%   of the matlab/ folders with the "A = ...;" line replaced.
%
%   Numbers are written with 17 significant digits, so they round-trip
%   exactly. A complex array becomes {"re": ..., "im": ...}; a matrix is a
%   list of rows; a vector is a flat list. Inf and NaN are written as the
%   Infinity/NaN tokens that Python's json module accepts.
%
%   export_refs(courseDir, outDir, aNumbers) chooses the A-numbers.

    if nargin < 3
        aNumbers = [80 0];
    end
    if ~exist(outDir, 'dir')
        mkdir(outDir);
    end

    scripts = { ...
        'S1',         fullfile('Assignment1', 'matlab', 'S1.m'); ...
        'S2',         fullfile('Assignment2', 'matlab', 'S2.m'); ...
        'S3',         fullfile('AssignmentS3', 'matlab', 'S3.m'); ...
        'LF',         fullfile('AssignmentLF', 'matlab', 'LF.m'); ...
        'LF_q_rated', fullfile('AssignmentLF', 'q_shunt_rated', 'matlab', 'LF.m')};

    for A = aNumbers
        tmpRoot = tempname;
        cleaner = onCleanup(@() rmdir(tmpRoot, 's'));
        copyTree(courseDir, tmpRoot, scripts(:, 2), A);

        refs = struct();
        refs.meta = struct( ...
            'A', A, ...
            'generator', 'tools/refs/export_refs.m', ...
            'source', 'MATLAB reference solutions of the EG2100 assignments', ...
            'matlab', version);
        for k = 1:size(scripts, 1)
            clear S1_pu S2_pu  % the helpers exist in every temporary copy
            fprintf('A = %d: running %s\n', A, scripts{k, 2});
            ws = runScript(fullfile(tmpRoot, scripts{k, 2}));
            refs.(scripts{k, 1}) = numericFields(ws);
        end

        outFile = fullfile(outDir, sprintf('refs_A%d.json', A));
        fid = fopen(outFile, 'w');
        fprintf(fid, '%s\n', encode(refs, ''));
        fclose(fid);
        fprintf('Wrote %s\n', outFile);
        clear cleaner
    end
end

%% ------------------------------------------------------------------------
function copyTree(courseDir, tmpRoot, scriptFiles, A)
%COPYTREE  Copy each script's matlab/ folder, keeping the relative layout
%   that S1_pu.m and S2_pu.m rely on, and set the A-number in the copies.
    for k = 1:numel(scriptFiles)
        srcDir = fullfile(courseDir, fileparts(scriptFiles{k}));
        dstDir = fullfile(tmpRoot, fileparts(scriptFiles{k}));
        if ~exist(dstDir, 'dir')
            mkdir(dstDir);
            copyfile(fullfile(srcDir, '*.m'), dstDir);
        end
        target = fullfile(tmpRoot, scriptFiles{k});
        txt = fileread(target);
        if isempty(regexp(txt, '^A = -?\d+;', 'lineanchors', 'once'))
            error('export_refs:noANumber', 'No "A = ...;" line in %s', target);
        end
        newTxt = regexprep(txt, '^A = -?\d+;', sprintf('A = %d;', A), ...
                           'lineanchors', 'once');
        fid = fopen(target, 'w');
        fprintf(fid, '%s', newTxt);
        fclose(fid);
    end
end

function s = runScript(scriptPath__)
%RUNSCRIPT  Run a script in this disposable workspace and return its
%   variables. The script starts with CLEAR, which wipes this workspace
%   first, so nothing local survives except what the script creates.
    evalc(['run(''' scriptPath__ ''')']);
    names__ = who;
    s = struct();
    for k__ = 1:numel(names__)
        s.(names__{k__}) = eval(names__{k__});
    end
end

function out = numericFields(ws)
%NUMERICFIELDS  Keep the numeric variables (structs from earlier
%   assignments, strings, function handles and options objects are dropped).
    out = struct();
    names = fieldnames(ws);
    for k = 1:numel(names)
        v = ws.(names{k});
        if isnumeric(v) && ~isempty(v) && ndims(v) <= 2
            out.(names{k}) = double(v);
        end
    end
end

%% ------------------------------------------------------------------------
function txt = encode(v, indent)
%ENCODE  Minimal JSON encoder with round-trip number precision.
    inner = [indent '  '];
    if isstruct(v)
        names = fieldnames(v);
        parts = cell(1, numel(names));
        for k = 1:numel(names)
            parts{k} = sprintf('%s"%s": %s', inner, names{k}, ...
                               encode(v.(names{k}), inner));
        end
        txt = sprintf('{\n%s\n%s}', strjoin(parts, sprintf(',\n')), indent);
    elseif ischar(v)
        txt = ['"' strrep(strrep(v, '\', '\\'), '"', '\"') '"'];
    elseif isnumeric(v) && ~isreal(v)
        txt = sprintf('{"re": %s, "im": %s}', encodeReal(real(v)), ...
                      encodeReal(imag(v)));
    else
        txt = encodeReal(double(v));
    end
end

function txt = encodeReal(x)
    if isscalar(x)
        txt = num(x);
    elseif isvector(x)
        txt = ['[' strjoin(arrayfun(@num, x(:).', 'UniformOutput', false), ', ') ']'];
    else
        rows = cell(1, size(x, 1));
        for r = 1:size(x, 1)
            rows{r} = ['[' strjoin(arrayfun(@num, x(r, :), 'UniformOutput', false), ', ') ']'];
        end
        txt = ['[' strjoin(rows, ', ') ']'];
    end
end

function s = num(x)
    if isnan(x)
        s = 'NaN';
    elseif isinf(x)
        if x > 0, s = 'Infinity'; else, s = '-Infinity'; end
    else
        s = sprintf('%.17g', x);
    end
end
